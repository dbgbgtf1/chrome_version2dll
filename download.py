#!/usr/bin/env python3
import gzip
import math
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener, urlopen


DEFAULT_BINARY_DIR = "binary"
DEFAULT_PROXY = None
DEFAULT_TIMEOUT = 30.0
DEFAULT_GZIP_RANGE_WORKERS = 8
DEFAULT_GZIP_RANGE_CHUNK_SIZE = 16 * 1024 * 1024


@dataclass(frozen=True)
class init_download_target:
    label: str
    url: str
    output_path: Path

    def __post_init__(self):
        print(f"url: {self.url}")
        print(f"output_path: {self.output_path}")


def normalize_proxy(proxy):
    proxy = proxy.strip()
    if "://" not in proxy:
        proxy = "http://" + proxy
    return proxy


def build_network_attempts(proxy=None):
    if proxy:
        return [("proxy", proxy)]
    return [("direct", None)]


def describe_network_attempt(mode, proxy=None):
    if proxy:
        return f"{mode} {normalize_proxy(proxy)}"
    return mode


def read_url(url, timeout, proxy=None, size=None, headers=None):
    request = Request(url, headers=headers or {}) if headers else url
    if proxy:
        normalized_proxy = normalize_proxy(proxy)
        opener = build_opener(
            ProxyHandler({"http": normalized_proxy, "https": normalized_proxy})
        )
        with opener.open(request, timeout=timeout) as response:
            return response.read(size)

    with urlopen(request, timeout=timeout) as response:
        return response.read(size)


def read_url_with_proxy_fallback(url, proxy, timeout, size=None, headers=None):
    attempts = build_network_attempts(proxy)

    for index, (_mode, attempt_proxy) in enumerate(attempts):
        try:
            return read_url(
                url,
                timeout,
                proxy=attempt_proxy,
                size=size,
                headers=headers,
            )
        except Exception:
            if index + 1 >= len(attempts):
                raise

            continue


def prompt_for_download(targets, binary_dir):
    answer = input(f"Download {len(targets)} files to {binary_dir}? [y/N] ")
    answer = answer.strip().lstrip("\ufeff").lower()
    return answer in ("y", "yes")


def run_curl(url, output_path, timeout, proxy=None):
    command = [
        "curl",
        "--location",
        "--compressed",
        "--retry",
        "3",
        "--max-time",
        str(timeout),
        "--output",
        str(output_path),
    ]

    if proxy:
        command.extend(["--proxy", normalize_proxy(proxy)])

    command.append(url)
    print(command)
    result = subprocess.run(command)
    if result.returncode != 0:
        raise RuntimeError(f"curl exited with code {result.returncode}")


def _open_request(url, timeout, proxy=None, headers=None):
    request = Request(url, headers=headers or {})
    if proxy:
        normalized_proxy = normalize_proxy(proxy)
        opener = build_opener(
            ProxyHandler({"http": normalized_proxy, "https": normalized_proxy})
        )
        return opener.open(request, timeout=timeout)

    return urlopen(request, timeout=timeout)


def _read_compressed_size(url, timeout, proxy=None):
    headers = {
        "Accept-Encoding": "gzip",
        "Range": "bytes=0-0",
    }
    with _open_request(url, timeout, proxy=proxy, headers=headers) as response:
        if response.status != 206:
            raise RuntimeError(f"range probe returned HTTP {response.status}")

        content_encoding = response.headers.get("Content-Encoding", "")
        if content_encoding.lower() != "gzip":
            raise RuntimeError(
                f"range probe returned Content-Encoding {content_encoding!r}"
            )

        content_range = response.headers.get("Content-Range", "")
        _range_spec, _sep, total_size = content_range.rpartition("/")
        if not total_size.isdigit():
            raise RuntimeError(f"cannot parse Content-Range: {content_range!r}")

        return int(total_size)


def _download_gzip_range_part(
    url,
    part_path,
    start,
    end,
    timeout,
    proxy=None,
    retries=3,
):
    expected_size = end - start + 1
    last_error = None

    for attempt in range(1, retries + 1):
        try:
            command = [
                "curl",
                "--location",
                "--fail",
                "--silent",
                "--show-error",
                "--retry",
                "5",
                "--retry-all-errors",
                "--retry-delay",
                "1",
                "--connect-timeout",
                "30",
                "--max-time",
                str(timeout),
                "--header",
                "Accept-Encoding: gzip",
                "--range",
                f"{start}-{end}",
                "--output",
                str(part_path),
                url,
            ]
            if proxy:
                command[1:1] = ["--proxy", normalize_proxy(proxy)]

            result = subprocess.run(command, capture_output=True, text=True)
            if result.returncode != 0:
                stderr = result.stderr.strip()
                raise RuntimeError(
                    f"curl exited with code {result.returncode}"
                    + (f": {stderr}" if stderr else "")
                )

            actual_size = part_path.stat().st_size
            if actual_size != expected_size:
                raise RuntimeError(
                    f"expected {expected_size} bytes, got {actual_size} bytes"
                )
            return
        except Exception as exc:
            last_error = exc
            if part_path.exists():
                part_path.unlink()
            if attempt == retries:
                break

    raise RuntimeError(
        f"failed to download range {start}-{end}: {last_error}"
    ) from last_error


class _PartSequenceReader:
    def __init__(self, part_paths):
        self._part_paths = iter(part_paths)
        self._current = None

    def close(self):
        if self._current is not None:
            self._current.close()
            self._current = None

    def readable(self):
        return True

    def read(self, size=-1):
        if size == 0:
            return b""

        chunks = []
        remaining = size

        while size < 0 or remaining > 0:
            if self._current is None:
                try:
                    self._current = next(self._part_paths).open("rb")
                except StopIteration:
                    break

            chunk = self._current.read(remaining if size > 0 else -1)
            if chunk:
                chunks.append(chunk)
                if size > 0:
                    remaining -= len(chunk)
                continue

            self._current.close()
            self._current = None

        return b"".join(chunks)


def run_gzip_range_download(
    url,
    output_path,
    timeout,
    proxy=None,
    workers=DEFAULT_GZIP_RANGE_WORKERS,
    chunk_size=DEFAULT_GZIP_RANGE_CHUNK_SIZE,
):
    if workers < 1:
        raise ValueError("gzip range workers must be at least 1")
    if chunk_size < 1:
        raise ValueError("gzip range chunk size must be at least 1")

    compressed_size = _read_compressed_size(url, timeout, proxy=proxy)
    part_count = math.ceil(compressed_size / chunk_size)
    part_dir = output_path.with_name(output_path.name + ".parts")
    temp_output_path = output_path.with_name(output_path.name + ".tmp")

    if part_dir.exists():
        shutil.rmtree(part_dir)
    if temp_output_path.exists():
        temp_output_path.unlink()
    part_dir.mkdir(parents=True)

    print(
        f"Downloading compressed gzip stream: {compressed_size} bytes, "
        f"{part_count} chunks, {workers} workers"
    )

    part_paths = [part_dir / f"{index:05d}.part" for index in range(part_count)]
    ranges = []
    for index, part_path in enumerate(part_paths):
        start = index * chunk_size
        end = min(start + chunk_size - 1, compressed_size - 1)
        ranges.append((index, part_path, start, end))

    try:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [
                executor.submit(
                    _download_gzip_range_part,
                    url,
                    part_path,
                    start,
                    end,
                    timeout,
                    proxy,
                )
                for _index, part_path, start, end in ranges
            ]

            finished = 0
            for future in as_completed(futures):
                future.result()
                finished += 1
                print(f"Downloaded chunk {finished}/{part_count}")

        reader = _PartSequenceReader(part_paths)
        try:
            with gzip.GzipFile(fileobj=reader) as gzip_file:
                with temp_output_path.open("wb") as output_file:
                    shutil.copyfileobj(gzip_file, output_file, length=1024 * 1024)
        finally:
            reader.close()

        temp_output_path.replace(output_path)
    finally:
        if temp_output_path.exists():
            temp_output_path.unlink()
        if part_dir.exists():
            shutil.rmtree(part_dir)


def _download_target(
    target,
    timeout,
    proxy,
    gzip_range,
    gzip_range_workers,
    gzip_range_chunk_size,
):
    if gzip_range:
        run_gzip_range_download(
            target.url,
            target.output_path,
            timeout,
            proxy=proxy,
            workers=gzip_range_workers,
            chunk_size=gzip_range_chunk_size,
        )
    else:
        run_curl(target.url, target.output_path, timeout, proxy=proxy)


def download_targets(
    targets,
    proxy,
    timeout,
    verifier,
    gzip_range=False,
    gzip_range_workers=DEFAULT_GZIP_RANGE_WORKERS,
    gzip_range_chunk_size=DEFAULT_GZIP_RANGE_CHUNK_SIZE,
):
    for target in targets:
        target.output_path.parent.mkdir(parents=True, exist_ok=True)
        print(f"Downloading {target.label}")

        attempts = build_network_attempts(proxy)

        for index, (mode, attempt_proxy) in enumerate(attempts):
            try:
                _download_target(
                    target,
                    timeout,
                    attempt_proxy,
                    gzip_range,
                    gzip_range_workers,
                    gzip_range_chunk_size,
                )
                verifier(target.output_path)
                print(f"Finished {target.output_path.name} via {mode}")
                break
            except Exception as exc:
                if target.output_path.exists():
                    target.output_path.unlink()

                if index + 1 >= len(attempts):
                    raise RuntimeError(
                        f"failed to download {target.label} via {mode}: {exc}"
                    ) from exc

                next_mode, next_proxy = attempts[index + 1]
                next_description = describe_network_attempt(next_mode, next_proxy)
                print(
                    f"{mode.capitalize()} download failed for {target.label}: {exc}. "
                    f"Retrying via {next_description}."
                )
