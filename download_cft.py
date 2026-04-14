#!/usr/bin/env python3
import argparse
import json
import subprocess
import sys
import zipfile
from pathlib import Path


DEFAULT_HISTORY_FILE = "cache_history_versions"
DEFAULT_DOWNLOADS_FILE = "cft_version_with_downloads.json"
DEFAULT_BINARY_DIR = "binary"
DEFAULT_PROXY = "127.0.0.1:10808"
DEFAULT_TIMEOUT = 30.0


def parse_args():
    parser = argparse.ArgumentParser(
        description="Extract win64 Chrome download URLs for a Chrome version range."
    )
    parser.add_argument("version_range", help="Chrome major version range, for example: 120-121")
    parser.add_argument("--history", default=DEFAULT_HISTORY_FILE, help="History cache file")
    parser.add_argument(
        "--downloads",
        default=DEFAULT_DOWNLOADS_FILE,
        help="Chrome for Testing downloads JSON file",
    )
    parser.add_argument("--binary-dir", default=DEFAULT_BINARY_DIR, help="Download directory")
    parser.add_argument("--proxy", default=DEFAULT_PROXY, help="Fallback HTTP/HTTPS proxy")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="Download timeout in seconds")
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


def build_url_map(downloads_data):
    url_map = {}
    for item in downloads_data.get("versions", []):
        version = item.get("version")
        if not version:
            continue

        for download in item.get("downloads", {}).get("chrome", []):
            if download.get("platform") == "win64" and download.get("url"):
                url_map[version] = download["url"]
                break
    return url_map


def normalize_proxy(proxy):
    proxy = proxy.strip()
    if "://" not in proxy:
        proxy = "http://" + proxy
    return proxy


def verify_zip_file(output_path):
    try:
        with zipfile.ZipFile(output_path) as zip_file:
            bad_file = zip_file.testzip()
    except zipfile.BadZipFile as exc:
        raise RuntimeError("downloaded file is not a valid zip") from exc

    if bad_file:
        raise RuntimeError(f"zip integrity check failed at {bad_file}")


def run_curl(url, output_path, timeout, proxy=None):
    command = [
        "curl",
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


def download_files(version_urls, binary_dir, proxy, timeout):
    binary_path = Path(binary_dir)
    binary_path.mkdir(parents=True, exist_ok=True)

    for version, url in version_urls:
        output_path = binary_path / f"{version}-chrome-win64.zip"
        print(f"Downloading {version} -> {output_path}")

        try:
            run_curl(url, output_path, timeout)
            verify_zip_file(output_path)
            print(f"Finished {version} via direct")
            continue
        except Exception as exc:
            if output_path.exists():
                output_path.unlink()
            normalized_proxy = normalize_proxy(proxy)
            print(f"Direct download failed for {version}: {exc}. Retrying via proxy {normalized_proxy}.")

        try:
            run_curl(url, output_path, timeout, proxy=proxy)
            verify_zip_file(output_path)
            print(f"Finished {version} via proxy")
        except Exception as exc:
            if output_path.exists():
                output_path.unlink()
            raise RuntimeError(f"failed to download {version} via proxy: {exc}") from exc


def main():
    args = parse_args()

    try:
        start_major, end_major = parse_version_range(args.version_range)
        history_data = load_json(args.history)
        downloads_data = load_json(args.downloads)
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
    url_map = build_url_map(downloads_data)

    version_urls = []
    for version in target_versions:
        url = url_map.get(version)
        if url:
            version_urls.append((version, url))

    if not version_urls:
        print("No download urls found.")
        return 0

    for _, url in version_urls:
        print(url)

    answer = input(f"Download {len(version_urls)} files to {args.binary_dir}? [y/N] ")
    answer = answer.strip().lstrip("\ufeff").lower()
    if answer not in ("y", "yes"):
        print("Canceled.")
        return 0

    try:
        download_files(version_urls, args.binary_dir, args.proxy, args.timeout)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"Downloaded {len(version_urls)} files to {args.binary_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
