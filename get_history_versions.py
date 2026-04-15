#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError

from download import DEFAULT_PROXY, DEFAULT_TIMEOUT, read_url_with_proxy_fallback


URL_TEMPLATE = (
    "https://versionhistory.googleapis.com/v1/chrome/platforms/{platform}/channels/{channel}/versions?pageSize=1000"
)
CHANNELS = ("stable", "extended", "beta", "canary", "dev")
PLATFORM_TYPES = {
    "webview": "WEBVIEW",
    "lacros_arm64": "LACROS_ARM64",
    "linux": "LINUX",
    "win": "WIN",
    "android": "ANDROID",
    "win64": "WIN64",
    "lacros": "LACROS",
    "ios": "IOS",
    "fuchsia": "FUCHSIA",
    "mac": "MAC",
    "lacros_arm32": "LACROS_ARM32",
    "chromeos": "CHROMEOS",
    "win_arm64": "WIN_ARM64",
    "mac_arm64": "MAC_ARM64",
}
PLATFORM_ALIASES = {
    **{platform: platform for platform in PLATFORM_TYPES},
    **{platform_type.lower(): platform for platform, platform_type in PLATFORM_TYPES.items()},
    **{f"chrome/platforms/{platform}": platform for platform in PLATFORM_TYPES},
}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Fetch Chrome version history into a local cache file."
    )
    parser.add_argument(
        "--channel",
        default="stable",
        choices=CHANNELS,
        help="Chrome channel",
    )
    parser.add_argument(
        "--platform",
        default="win64",
        type=normalize_platform,
        metavar="PLATFORM",
        help=(
            "Chrome platform slug, platformType, or full resource name. "
            f"Supported slugs: {', '.join(PLATFORM_TYPES)}"
        ),
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output file path relative to the current directory",
    )
    parser.add_argument("--proxy", default=DEFAULT_PROXY, help="HTTP/HTTPS proxy")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="Request timeout in seconds")
    return parser.parse_args()


def normalize_platform(platform):
    normalized = platform.strip().lower()
    try:
        return PLATFORM_ALIASES[normalized]
    except KeyError as exc:
        supported = ", ".join(PLATFORM_TYPES)
        raise argparse.ArgumentTypeError(
            f"unsupported platform {platform!r}. Supported slugs: {supported}"
        ) from exc


def build_url(platform, channel):
    return URL_TEMPLATE.format(platform=platform, channel=channel)


def fetch_text(url, timeout, proxy=None):
    headers = {
        "Accept": "application/json",
        "User-Agent": "get_history_versions.py",
    }

    try:
        data = read_url_with_proxy_fallback(url, proxy, timeout, headers=headers)
        return data.decode("utf-8")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body}") from exc
    except URLError as exc:
        raise RuntimeError(str(exc.reason)) from exc
    except TimeoutError as exc:
        raise RuntimeError("request timed out") from exc


def write_cache(output, text):
    output_path = Path.cwd() / output
    output_path.write_text(text, encoding="utf-8")
    return output_path


def resolve_output_path(channel, output):
    if output:
        return output
    return f"cache_{channel}_history_versions"


def main():
    args = parse_args()
    url = build_url(args.platform, args.channel)
    output = resolve_output_path(args.channel, args.output)

    try:
        response_text = fetch_text(url, args.timeout, proxy=args.proxy)
    except RuntimeError as exc:
        print(f"Request failed: {exc}", file=sys.stderr)
        return 1

    try:
        json.loads(response_text)
    except json.JSONDecodeError as exc:
        print(f"Response is not valid JSON: {exc}", file=sys.stderr)
        return 1

    output_path = write_cache(output, response_text)
    print(f"Saved {url} to {output_path}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
