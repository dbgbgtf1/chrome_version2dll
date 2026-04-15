#!/usr/bin/env python3
import argparse
import datetime
import heapq
import json
import os
import struct
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

from download import (
    DEFAULT_BINARY_DIR,
    DEFAULT_PROXY,
    DEFAULT_TIMEOUT,
    download_targets,
    init_download_target,
    prompt_for_download,
    read_url,
    read_url_with_proxy_fallback,
)


DEFAULT_MAX_GRAPH_VISITS = 512
ARCH_MACHINES = {
    "x86": 0x014C,
    "x64": 0x8664,
    "arm64": 0xAA64,
}
DEFAULT_DLL_BINARY_DIR = str(Path(DEFAULT_BINARY_DIR) / "dll")
GITHUB_API_BASE_URL = "https://api.github.com/repos/chromium/chromium"
GITHUB_RAW_BASE_URL = "https://raw.githubusercontent.com/chromium/chromium"
SYMSRV_PREFIX_URL = "https://chromium-browser-symsrv.commondatastorage.googleapis.com/?prefix=chrome.dll"
SYMSRV_OBJECT_URL = "https://chromium-browser-symsrv.commondatastorage.googleapis.com"


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Compute Chromium official Windows build timestamps for all cached stable "
            "tags in a major-version range. This follows build/compute_build_timestamp.py: "
            "LASTCHANGE.committime + chrome/VERSION PATCH."
        )
    )
    parser.add_argument(
        "version_range",
        help="Major version range, for example: 120-121",
    )
    parser.add_argument(
        "--channel",
        default="stable",
        help="Chrome channel used to select the local cache file",
    )
    parser.add_argument("--arch", choices=ARCH_MACHINES, default="x64")
    parser.add_argument(
        "--binary-dir",
        default=DEFAULT_DLL_BINARY_DIR,
        help="Directory to write downloaded chrome.dll files",
    )
    parser.add_argument("--proxy", default=DEFAULT_PROXY, help="HTTP/HTTPS proxy")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="Request timeout in seconds")
    return parser.parse_args()


def parse_version_range(text):
    parts = [part.strip() for part in text.split("-", 1)]
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(f"invalid version range: {text!r}")

    start_major = int(parts[0])
    end_major = int(parts[1])
    if start_major > end_major:
        raise ValueError(f"invalid version range: {text!r}")
    return start_major, end_major


def resolve_cache_path(channel):
    channel = channel.strip()
    singular_path = Path.cwd() / f"cache_{channel}_history_version"
    if singular_path.exists():
        return singular_path

    plural_path = Path.cwd() / f"cache_{channel}_history_versions"
    return plural_path


def read_cached_versions(channel):
    cache_path = resolve_cache_path(channel)
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    return payload.get("versions", [])


def select_tags_in_range(version_range_text, channel):
    start_major, end_major = parse_version_range(version_range_text)
    selected_tags = []

    for item in read_cached_versions(channel):
        tag = item.get("version", "").strip()
        if not tag:
            continue

        major_text = tag.split(".", 1)[0]
        major = int(major_text)
        if start_major <= major <= end_major:
            selected_tags.append(tag)

    return selected_tags


def parse_json(data):
    return json.loads(data.decode("utf-8"))


def get_github_token():
    token = os.environ.get("github_token") or os.environ.get("GITHUB_TOKEN")
    if not token:
        return None
    token = token.strip()
    return token or None


def build_github_api_headers():
    token = get_github_token()
    if not token:
        return None

    return {
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def parse_github_time(time_text):
    commit_time = datetime.datetime.strptime(time_text, "%Y-%m-%dT%H:%M:%SZ")
    commit_time = commit_time.replace(tzinfo=datetime.timezone.utc)
    return int(commit_time.timestamp())


def read_commit_timestamp(commit):
    return parse_github_time(commit["committer"]["date"])


def read_tag_commit_sha(tag, proxy, timeout):
    quoted_tag = urllib.parse.quote(tag, safe="")
    url = f"{GITHUB_API_BASE_URL}/git/ref/tags/{quoted_tag}"
    payload = parse_json(
        read_url_with_proxy_fallback(
            url,
            proxy,
            timeout,
            headers=build_github_api_headers(),
        )
    )
    if payload.get("object", {}).get("type") != "commit":
        raise RuntimeError(f"tag {tag} does not point to a commit")
    return payload["object"]["sha"]


def read_commit(sha, proxy, timeout, commit_cache):
    if sha in commit_cache:
        return commit_cache[sha]

    url = f"{GITHUB_API_BASE_URL}/git/commits/{sha}"
    commit = parse_json(
        read_url_with_proxy_fallback(
            url,
            proxy,
            timeout,
            headers=build_github_api_headers(),
        )
    )
    commit_cache[sha] = commit
    return commit


def read_version_file(git_ref, proxy, timeout):
    quoted_ref = urllib.parse.quote(git_ref, safe="")
    url = f"{GITHUB_RAW_BASE_URL}/{quoted_ref}/chrome/VERSION"
    return read_url_with_proxy_fallback(url, proxy, timeout).decode("utf-8")


def parse_version_fields(version_text):
    version_fields = {}
    for line in version_text.splitlines():
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        version_fields[key.strip()] = value.strip()

    required_fields = ("MAJOR", "MINOR", "BUILD", "PATCH")
    missing_fields = [field for field in required_fields if field not in version_fields]
    if missing_fields:
        raise ValueError(
            "missing fields in chrome/VERSION: " + ", ".join(missing_fields)
        )

    return version_fields


def build_version_tag(version_fields):
    return ".".join(
        version_fields[field] for field in ("MAJOR", "MINOR", "BUILD", "PATCH")
    )


def read_cached_version_fields(git_ref, proxy, timeout, version_cache):
    if git_ref not in version_cache:
        version_text = read_version_file(git_ref, proxy, timeout)
        version_cache[git_ref] = parse_version_fields(version_text)

    return version_cache[git_ref]


def commit_has_change_id(commit):
    return any(
        line.startswith("Change-Id:")
        for line in commit.get("message", "").splitlines()
    )


def resolve_lastchange_timestamp(tag_commit_sha, proxy, timeout, commit_cache):
    # Chromium's build/util/lastchange.py effectively runs:
    #   git log -1 --grep=^Change-Id: <start_commit>
    # That walks the reachable history, not just the first-parent chain. We
    # approximate that here by exploring all reachable parents while preferring
    # newer commits, which is much closer than a linear first-parent fallback.
    seen = set()
    start_commit = read_commit(tag_commit_sha, proxy, timeout, commit_cache)
    frontier = [(-read_commit_timestamp(start_commit), 0, tag_commit_sha)]
    visits = 0

    while frontier and visits < DEFAULT_MAX_GRAPH_VISITS:
        _negative_timestamp, depth, current_sha = heapq.heappop(frontier)
        if current_sha in seen:
            continue

        seen.add(current_sha)
        visits += 1
        commit = read_commit(current_sha, proxy, timeout, commit_cache)
        if commit_has_change_id(commit):
            return depth, read_commit_timestamp(commit)

        for parent in commit.get("parents") or []:
            parent_sha = parent["sha"]
            if parent_sha in seen:
                continue
            parent_commit = read_commit(parent_sha, proxy, timeout, commit_cache)
            heapq.heappush(
                frontier,
                (-read_commit_timestamp(parent_commit), depth + 1, parent_sha),
            )

    raise RuntimeError(
        "failed to find a Change-Id commit while traversing reachable history "
        f"within {DEFAULT_MAX_GRAPH_VISITS} commits"
    )


def compute_timestamp_for_commit(commit_sha, proxy, timeout, commit_cache, version_cache):
    version_fields = read_cached_version_fields(commit_sha, proxy, timeout, version_cache)
    patch = int(version_fields["PATCH"])
    _lastchange_depth, lastchange_timestamp = resolve_lastchange_timestamp(
        commit_sha, proxy, timeout, commit_cache
    )
    return {
        "resolved_tag": build_version_tag(version_fields),
        "timestamp": lastchange_timestamp + patch,
    }


def compute_timestamp_for_commit_with_patch(
    commit_sha, patch, proxy, timeout, commit_cache, version_cache
):
    version_fields = read_cached_version_fields(commit_sha, proxy, timeout, version_cache)
    _lastchange_depth, lastchange_timestamp = resolve_lastchange_timestamp(
        commit_sha, proxy, timeout, commit_cache
    )
    return {
        "base_tag": build_version_tag(version_fields),
        "timestamp": lastchange_timestamp + patch,
    }


def build_dll_url(timestamp):
    return f"{SYMSRV_PREFIX_URL}/{timestamp:X}"


def url_exists(url, proxy, timeout):
    response = read_url(url, timeout, proxy=proxy)
    return b"<Contents>" in response


def read_pe_machine_from_url(url, proxy, timeout):
    header = read_url_with_proxy_fallback(url, proxy, timeout, size=512)

    pe_offset = struct.unpack_from("<I", header, 0x3C)[0]
    if header[pe_offset : pe_offset + 4] != b"PE\0\0":
        raise ValueError(f"invalid PE signature in {url}")

    return struct.unpack_from("<H", header, pe_offset + 4)[0]


def verify_pe_file(output_path):
    with output_path.open("rb") as file_obj:
        if file_obj.read(2) != b"MZ":
            raise RuntimeError("downloaded file is not a valid PE")


def resolve_symbol_url(list_url, machine, proxy, timeout):
    root = ET.fromstring(read_url(list_url, timeout, proxy=proxy))

    for content in root.findall("{*}Contents"):
        key = content.findtext("{*}Key")
        if not key:
            continue

        symbol_url = f"{SYMSRV_OBJECT_URL}/{key}"
        if read_pe_machine_from_url(symbol_url, proxy, timeout) == machine:
            return symbol_url

    raise RuntimeError("no matching symbol object found in bucket listing")


def find_existing_url_for_tag(tag, proxy, timeout, commit_cache, version_cache):
    requested_sha = read_tag_commit_sha(tag, proxy, timeout)
    requested_version_fields = read_cached_version_fields(
        requested_sha, proxy, timeout, version_cache
    )
    requested_patch = int(requested_version_fields["PATCH"])
    requested_version_tag = build_version_tag(requested_version_fields)
    current_sha = requested_sha

    while True:
        timestamp_info = compute_timestamp_for_commit_with_patch(
            current_sha,
            requested_patch,
            proxy,
            timeout,
            commit_cache,
            version_cache,
        )
        url = build_dll_url(timestamp_info["timestamp"])
        if url_exists(url, proxy, timeout):
            return {
                "requested_tag": tag,
                "requested_sha": requested_sha,
                "resolved_tag": requested_version_tag,
                "resolved_sha": current_sha,
                "base_tag": timestamp_info["base_tag"],
                "timestamp": timestamp_info["timestamp"],
                "list_url": url,
            }

        commit = read_commit(current_sha, proxy, timeout, commit_cache)
        parents = commit.get("parents") or []
        if not parents:
            raise RuntimeError("reached a root commit before finding a matching url")
        current_sha = parents[0]["sha"]


def build_output_name(tag, arch):
    return f"{tag}-{arch}-chrome.dll"


def main():
    args = parse_args()
    commit_cache = {}
    version_cache = {}
    machine = ARCH_MACHINES[args.arch]
    targets = []
    seen_symbol_urls = set()
    exit_code = 0

    try:
        tags = select_tags_in_range(args.version_range, args.channel)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if not tags:
        print("no cached versions matched the requested range", file=sys.stderr)
        return 1

    for tag in reversed(tags):
        try:
            hit = find_existing_url_for_tag(
                tag, args.proxy, args.timeout, commit_cache, version_cache
            )
            url = resolve_symbol_url(
                hit["list_url"], machine, args.proxy, args.timeout
            )
        except Exception as exc:
            print(f"{tag}: {exc}", file=sys.stderr)
            exit_code = 1
            continue

        if url in seen_symbol_urls:
            print(
                f"{hit['requested_tag']} match to {hit['resolved_tag']}, "
                "perhaps check source code for diff?"
            )
            continue
        seen_symbol_urls.add(url)

        output_name = build_output_name(hit["resolved_tag"], args.arch)

        targets.append(
            init_download_target(
                label=(f"{hit['requested_tag']} -> {hit['resolved_tag']}"),
                url=url,
                output_path=Path(args.binary_dir) / output_name,
            )
        )

    if not targets:
        print("No dll urls found.")
        return exit_code or 1

    if not prompt_for_download(targets, args.binary_dir):
        print("Canceled.")
        return exit_code

    try:
        download_targets(targets, args.proxy, args.timeout, verify_pe_file)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"Downloaded {len(targets)} files to {args.binary_dir}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
