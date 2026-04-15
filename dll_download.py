#!/usr/bin/env python3
import argparse
import datetime
import json
import sys
import urllib.parse
from pathlib import Path

from download import DEFAULT_PROXY, DEFAULT_TIMEOUT, read_url, read_url_with_proxy_fallback


DEFAULT_MAX_DEPTH = 32
CACHE_FILE = "cache_stable_history_versions"
GITHUB_API_BASE_URL = "https://api.github.com/repos/chromium/chromium"
GITHUB_RAW_BASE_URL = "https://raw.githubusercontent.com/chromium/chromium"
SYMSRV_PREFIX_URL = "https://chromium-browser-symsrv.commondatastorage.googleapis.com/?prefix=chrome.dll"


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


def read_cached_versions():
    cache_path = Path.cwd() / CACHE_FILE
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    return payload.get("versions", [])


def select_tags_in_range(version_range_text):
    start_major, end_major = parse_version_range(version_range_text)
    selected_tags = []

    for item in read_cached_versions():
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


def parse_github_time(time_text):
    commit_time = datetime.datetime.strptime(time_text, "%Y-%m-%dT%H:%M:%SZ")
    commit_time = commit_time.replace(tzinfo=datetime.timezone.utc)
    return int(commit_time.timestamp())


def read_tag_commit_sha(tag, proxy, timeout):
    quoted_tag = urllib.parse.quote(tag, safe="")
    url = f"{GITHUB_API_BASE_URL}/git/ref/tags/{quoted_tag}"
    payload = parse_json(read_url_with_proxy_fallback(url, proxy, timeout))
    if payload.get("object", {}).get("type") != "commit":
        raise RuntimeError(f"tag {tag} does not point to a commit")
    return payload["object"]["sha"]


def read_commit(sha, proxy, timeout, commit_cache):
    if sha in commit_cache:
        return commit_cache[sha]

    url = f"{GITHUB_API_BASE_URL}/git/commits/{sha}"
    commit = parse_json(read_url_with_proxy_fallback(url, proxy, timeout))
    commit_cache[sha] = commit
    return commit


def read_version_file(git_ref, proxy, timeout):
    quoted_ref = urllib.parse.quote(git_ref, safe="")
    url = f"{GITHUB_RAW_BASE_URL}/{quoted_ref}/chrome/VERSION"
    return read_url_with_proxy_fallback(url, proxy, timeout).decode("utf-8")


def parse_patch(version_text):
    for line in version_text.splitlines():
        if line.startswith("PATCH="):
            return int(line.split("=", 1)[1])
    raise ValueError("PATCH not found in chrome/VERSION")


def resolve_lastchange_timestamp(tag_commit_sha, proxy, timeout, commit_cache):
    current_sha = tag_commit_sha
    for depth in range(DEFAULT_MAX_DEPTH + 1):
        commit = read_commit(current_sha, proxy, timeout, commit_cache)
        message = commit.get("message", "")
        if "Change-Id:" in message:
            return depth, parse_github_time(commit["committer"]["date"])

        parents = commit.get("parents") or []
        if not parents:
            break
        current_sha = parents[0]["sha"]

    raise RuntimeError(
        f"failed to find a Change-Id commit within first-parent depth {DEFAULT_MAX_DEPTH}"
    )


def compute_timestamp_for_commit(commit_sha, proxy, timeout, commit_cache):
    version_text = read_version_file(commit_sha, proxy, timeout)
    patch = parse_patch(version_text)
    _depth, lastchange_timestamp = resolve_lastchange_timestamp(
        commit_sha, proxy, timeout, commit_cache
    )
    return lastchange_timestamp + patch


def build_dll_url(timestamp):
    return f"{SYMSRV_PREFIX_URL}/{timestamp:X}"


def url_exists(url, proxy, timeout):
    response = read_url(url, timeout, proxy=proxy)
    return b"<Contents>" in response


def find_existing_url_for_tag(tag, proxy, timeout, commit_cache):
    current_sha = read_tag_commit_sha(tag, proxy, timeout)

    while True:
        timestamp = compute_timestamp_for_commit(current_sha, proxy, timeout, commit_cache)
        url = build_dll_url(timestamp)
        if url_exists(url, proxy, timeout):
            return url

        commit = read_commit(current_sha, proxy, timeout, commit_cache)
        parents = commit.get("parents") or []
        if not parents:
            raise RuntimeError("reached a root commit before finding a matching url")
        current_sha = parents[0]["sha"]


def main():
    args = parse_args()
    commit_cache = {}
    exit_code = 0

    try:
        tags = select_tags_in_range(args.version_range)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if not tags:
        print("no cached versions matched the requested range", file=sys.stderr)
        return 1

    for tag in tags:
        try:
            url = find_existing_url_for_tag(tag, args.proxy, args.timeout, commit_cache)
        except Exception as exc:
            print(f"{tag}: {exc}", file=sys.stderr)
            exit_code = 1
            continue

        print(f"{tag}: {url}")

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
