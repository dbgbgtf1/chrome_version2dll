#!/usr/bin/env python3
import argparse
import json
import sys
import zipfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener, urlopen

from download import (
    DEFAULT_BINARY_DIR,
    DEFAULT_PROXY,
    DEFAULT_TIMEOUT,
    DownloadTarget,
    download_targets,
    normalize_proxy,
    prompt_for_download,
)


DEFAULT_HISTORY_FILE = "cache_stable_history_versions"
DEFAULT_CFT_BINARY_DIR = str(Path(DEFAULT_BINARY_DIR) / "cft")
URL_TEMPLATE = (
    "https://storage.googleapis.com/chrome-for-testing-public/"
    "{version}/win64/chrome-win64.zip"
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Probe and download win64 Chrome for Testing zips for a Chrome version range."
    )
    parser.add_argument("version_range", help="Chrome major version range, for example: 120-121")
    parser.add_argument("--history", default=DEFAULT_HISTORY_FILE, help="History cache file")
    parser.add_argument("--binary-dir", default=DEFAULT_CFT_BINARY_DIR, help="Download directory")
    parser.add_argument("--proxy", default=DEFAULT_PROXY, help="HTTP/HTTPS proxy")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="Probe and download timeout in seconds")
    return parser.parse_args()


def parse_version_range(version_range):
    parts = version_range.split("-", 1)
    if len(parts) != 2:
        raise ValueError("version range must look like 120-121")

    start = int(parts[0].strip())
    end = int(parts[1].strip())
    if start > end:
        start, end = end, start
    return start, end


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def major_version(version):
    return int(version.split(".", 1)[0])


def collect_target_versions(history_data, start_major, end_major):
    versions = []
    for item in history_data.get("versions", []):
        version = item.get("version")
        if not version:
            continue
        major = major_version(version)
        if start_major <= major <= end_major:
            versions.append(version)
    return versions


def build_download_url(version):
    return URL_TEMPLATE.format(version=version)


def _open_request(request, timeout, proxy=None):
    if proxy:
        normalized_proxy = normalize_proxy(proxy)
        opener = build_opener(
            ProxyHandler(
                {
                    "http": normalized_proxy,
                    "https": normalized_proxy,
                }
            )
        )
        return opener.open(request, timeout=timeout)

    return urlopen(request, timeout=timeout)


def probe_url(url, timeout, proxy=None):
    request = Request(
        url,
        headers={
            "Range": "bytes=0-0",
            "User-Agent": "cft_download.py",
        },
    )

    try:
        with _open_request(request, timeout, proxy=proxy) as response:
            return response.status in (200, 206)
    except HTTPError as exc:
        if exc.code in (403, 404):
            return False
        raise RuntimeError(f"HTTP {exc.code}") from exc
    except URLError as exc:
        raise RuntimeError(str(exc.reason)) from exc
    except TimeoutError as exc:
        raise RuntimeError("request timed out") from exc


def probe_url_with_proxy_fallback(url, proxy, timeout):
    if proxy:
        try:
            return probe_url(url, timeout, proxy=proxy)
        except RuntimeError as proxy_error:
            print(
                f"Proxy probe failed for {url}: {proxy_error}. "
                "Retrying direct."
            )
            return probe_url(url, timeout)

    return probe_url(url, timeout)


def verify_zip_file(output_path):
    try:
        with zipfile.ZipFile(output_path) as zip_file:
            bad_file = zip_file.testzip()
    except zipfile.BadZipFile as exc:
        raise RuntimeError("downloaded file is not a valid zip") from exc

    if bad_file:
        raise RuntimeError(f"zip integrity check failed at {bad_file}")


def main():
    args = parse_args()

    try:
        start_major, end_major = parse_version_range(args.version_range)
        history_data = load_json(args.history)
    except FileNotFoundError as exc:
        print(f"file not found: {exc.filename}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as exc:
        print(f"invalid json: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    target_versions = collect_target_versions(history_data, start_major, end_major)

    targets = []
    skipped_versions = []
    binary_path = Path(args.binary_dir)
    for version in target_versions:
        url = build_download_url(version)
        try:
            available = probe_url_with_proxy_fallback(url, args.proxy, args.timeout)
        except RuntimeError as exc:
            print(f"failed to probe {version}: {exc}", file=sys.stderr)
            return 1

        if available:
            targets.append(
                DownloadTarget(
                    label=version,
                    url=url,
                    output_path=binary_path / f"{version}-chrome-win64.zip",
                )
            )
        else:
            skipped_versions.append(version)

    if not targets:
        print("No download urls found.")
        return 0

    if not prompt_for_download(targets, args.binary_dir):
        print("Canceled.")
        return 0

    try:
        download_targets(targets, args.proxy, args.timeout, verify_zip_file)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if skipped_versions:
        print(f"Skipped {len(skipped_versions)} unavailable versions.")
    print(f"Downloaded {len(targets)} files to {args.binary_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
