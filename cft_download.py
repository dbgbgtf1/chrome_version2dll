#!/usr/bin/env python3
import argparse
import json
import sys
import zipfile
from pathlib import Path

from download import (
    DEFAULT_BINARY_DIR,
    DEFAULT_PROXY,
    DEFAULT_TIMEOUT,
    DownloadTarget,
    download_targets,
    prompt_for_download,
)


DEFAULT_HISTORY_FILE = "cache_stable_history_versions"
DEFAULT_DOWNLOADS_FILE = "cft_version_with_downloads.json"


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

    targets = []
    binary_path = Path(args.binary_dir)
    for version in target_versions:
        url = url_map.get(version)
        if url:
            targets.append(
                DownloadTarget(
                    label=version,
                    url=url,
                    output_path=binary_path / f"{version}-chrome-win64.zip",
                )
            )

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

    print(f"Downloaded {len(targets)} files to {args.binary_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
