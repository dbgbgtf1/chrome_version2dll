import argparse
import struct
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlencode

from download import (
    DEFAULT_BINARY_DIR,
    DEFAULT_PROXY,
    DEFAULT_TIMEOUT,
    DownloadTarget,
    download_targets,
    prompt_for_download,
    read_url_with_proxy_fallback,
)


ARCH_MACHINES = {
    "x86": 0x014C,
    "x64": 0x8664,
    "arm64": 0xAA64,
}


def read_pe_metadata(file_obj):
    file_obj.seek(0x3C)
    pe_offset = struct.unpack("<I", file_obj.read(4))[0]

    file_obj.seek(pe_offset)
    if file_obj.read(4) != b"PE\0\0":
        raise ValueError("invalid PE signature")

    # IMAGE_FILE_HEADER starts immediately after PE signature.
    file_obj.read(4)  # Machine + NumberOfSections
    timestamp = struct.unpack("<I", file_obj.read(4))[0]
    return timestamp


def read_pe_machine_from_url(url, proxy, timeout):
    header = read_url_with_proxy_fallback(url, proxy, timeout, size=512)

    pe_offset = struct.unpack_from("<I", header, 0x3C)[0]
    if header[pe_offset : pe_offset + 4] != b"PE\0\0":
        raise ValueError(f"invalid PE signature in {url}")

    return struct.unpack_from("<H", header, pe_offset + 4)[0]


def resolve_symbol_url(timestamp, machine, proxy, timeout):
    prefix = f"chrome.dll/{timestamp:X}"
    list_url = (
        "https://chromium-browser-symsrv.commondatastorage.googleapis.com/?"
        + urlencode({"prefix": prefix})
    )
    root = ET.fromstring(read_url_with_proxy_fallback(list_url, proxy, timeout))

    for content in root.findall("{*}Contents"):
        key = content.findtext("{*}Key")
        if not key:
            continue

        symbol_url = (
            "https://chromium-browser-symsrv.commondatastorage.googleapis.com/" + key
        )
        if read_pe_machine_from_url(symbol_url, proxy, timeout) == machine:
            return symbol_url

    raise ValueError(f"no matching symbol object for timestamp {timestamp:X}")


def extract_version(zip_path):
    name = zip_path.name
    marker = "-chrome-"
    if marker not in name:
        raise ValueError(f"cannot extract version from {name}")
    return name.split(marker, 1)[0]


def verify_pe_file(output_path):
    with output_path.open("rb") as file_obj:
        if file_obj.read(2) != b"MZ":
            raise RuntimeError("downloaded file is not a valid PE")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--arch", choices=ARCH_MACHINES, default="x64")
    parser.add_argument("--binary-dir", default=DEFAULT_BINARY_DIR)
    parser.add_argument("--proxy", default=DEFAULT_PROXY)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    args = parser.parse_args()

    binary_dir = Path(args.binary_dir)
    machine = ARCH_MACHINES[args.arch]
    targets = []

    try:
        for zip_path in sorted(binary_dir.glob("*.zip")):
            with zipfile.ZipFile(zip_path) as zf:
                dll_name = next(
                    name for name in zf.namelist() if name.lower().endswith("chrome.dll")
                )
                with zf.open(dll_name) as dll:
                    timestamp = read_pe_metadata(dll)

            version = extract_version(zip_path)
            url = resolve_symbol_url(timestamp, machine, args.proxy, args.timeout)
            targets.append(
                DownloadTarget(
                    label=version,
                    url=url,
                    output_path=binary_dir / f"{version}-{args.arch}-chrome.dll",
                )
            )
    except (ValueError, RuntimeError, zipfile.BadZipFile) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if not targets:
        print("No release urls found.")
        return 0

    if not prompt_for_download(targets, args.binary_dir):
        print("Canceled.")
        return 0

    try:
        download_targets(targets, args.proxy, args.timeout, verify_pe_file)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"Downloaded {len(targets)} files to {args.binary_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
