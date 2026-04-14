#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener


DEFAULT_URL = (
    "https://versionhistory.googleapis.com/v1/chrome/"
    "platforms/win64/channels/stable/versions?pageSize=1000"
)
DEFAULT_OUTPUT = "cache_history_versions"
DEFAULT_PROXY = "http://127.0.0.1:10808"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Fetch Chrome stable win64 version history into a local cache file."
    )
    parser.add_argument("--url", default=DEFAULT_URL, help="Version history API URL")
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT,
        help="Output file path relative to the current directory",
    )
    parser.add_argument(
        "--proxy",
        default=DEFAULT_PROXY,
        help="Fallback HTTP/HTTPS proxy used when direct network access fails",
    )
    parser.add_argument("--timeout", type=float, default=30.0, help="Request timeout in seconds")
    return parser.parse_args()


def normalize_proxy(proxy):
    proxy = proxy.strip()
    if "://" not in proxy:
        proxy = "http://" + proxy
    return proxy


def fetch_text(url, timeout, proxy=None):
    handlers = []
    if proxy:
        normalized_proxy = normalize_proxy(proxy)
        handlers.append(
            ProxyHandler(
                {
                    "http": normalized_proxy,
                    "https": normalized_proxy,
                }
            )
        )

    opener = build_opener(*handlers)
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "get_history_versions.py",
        },
    )

    try:
        with opener.open(request, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset)
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


def main():
    args = parse_args()

    try:
        response_text = fetch_text(args.url, args.timeout)
        network_mode = "direct"
    except RuntimeError as direct_error:
        print(
            f"Direct request failed: {direct_error}. Retrying with proxy {args.proxy}.",
            file=sys.stderr,
        )
        try:
            response_text = fetch_text(args.url, args.timeout, proxy=args.proxy)
            network_mode = f"proxy {normalize_proxy(args.proxy)}"
        except RuntimeError as proxy_error:
            print(f"Proxy request failed: {proxy_error}", file=sys.stderr)
            return 1

    try:
        json.loads(response_text)
    except json.JSONDecodeError as exc:
        print(f"Response is not valid JSON: {exc}", file=sys.stderr)
        return 1

    output_path = write_cache(args.output, response_text)
    print(f"Saved {args.url} to {output_path} via {network_mode}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
