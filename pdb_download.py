#!/usr/bin/env python3
import argparse
import struct
import sys
import uuid
from pathlib import Path

from download import (
    DEFAULT_BINARY_DIR,
    DEFAULT_GZIP_RANGE_CHUNK_SIZE,
    DEFAULT_GZIP_RANGE_WORKERS,
    DEFAULT_PROXY,
    DownloadTarget,
    download_targets,
    prompt_for_download,
)


SYMSRV_BASE_URL = "https://chromium-browser-symsrv.commondatastorage.googleapis.com"
DEFAULT_PDB_TIMEOUT = 600.0
DEFAULT_DLL_BINARY_DIR = str(Path(DEFAULT_BINARY_DIR) / "dll")
DEFAULT_PDB_BINARY_DIR = str(Path(DEFAULT_BINARY_DIR) / "pdb")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Download chrome.dll.pdb files for local chrome.dll files."
    )
    parser.add_argument(
        "dll_paths",
        nargs="*",
        type=Path,
        help="Optional chrome.dll paths. Defaults to binary/dll/*chrome.dll",
    )
    parser.add_argument(
        "--dll-dir",
        default=DEFAULT_DLL_BINARY_DIR,
        help="Directory containing chrome.dll files when dll_paths are omitted",
    )
    parser.add_argument(
        "--binary-dir",
        default=DEFAULT_PDB_BINARY_DIR,
        help="Directory to write downloaded PDB files",
    )
    parser.add_argument("--proxy", default=DEFAULT_PROXY)
    parser.add_argument("--timeout", type=float, default=DEFAULT_PDB_TIMEOUT)
    parser.add_argument(
        "--download-method",
        choices=("gzip-range", "curl"),
        default="gzip-range",
        help=(
            "gzip-range downloads the server-stored gzip stream in parallel and "
            "decompresses it locally. curl uses a single curl --compressed transfer."
        ),
    )
    parser.add_argument(
        "--gzip-range-workers",
        type=int,
        default=DEFAULT_GZIP_RANGE_WORKERS,
        help="Parallel workers for --download-method gzip-range.",
    )
    parser.add_argument(
        "--gzip-range-chunk-size",
        type=int,
        default=DEFAULT_GZIP_RANGE_CHUNK_SIZE,
        help="Compressed bytes per chunk for --download-method gzip-range.",
    )
    return parser.parse_args()


def read_pe_headers(data):
    if data[:2] != b"MZ":
        raise ValueError("invalid DOS signature")

    pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe_offset : pe_offset + 4] != b"PE\0\0":
        raise ValueError("invalid PE signature")

    coff_offset = pe_offset + 4
    number_of_sections = struct.unpack_from("<H", data, coff_offset + 2)[0]
    size_of_optional_header = struct.unpack_from("<H", data, coff_offset + 16)[0]

    optional_offset = coff_offset + 20
    magic = struct.unpack_from("<H", data, optional_offset)[0]
    if magic == 0x10B:
        data_directories_offset = optional_offset + 96
        number_of_rva_and_sizes = struct.unpack_from(
            "<I", data, optional_offset + 92
        )[0]
    elif magic == 0x20B:
        data_directories_offset = optional_offset + 112
        number_of_rva_and_sizes = struct.unpack_from(
            "<I", data, optional_offset + 108
        )[0]
    else:
        raise ValueError(f"unsupported optional header magic: 0x{magic:X}")

    section_table_offset = optional_offset + size_of_optional_header
    return {
        "number_of_sections": number_of_sections,
        "number_of_rva_and_sizes": number_of_rva_and_sizes,
        "data_directories_offset": data_directories_offset,
        "section_table_offset": section_table_offset,
    }


def read_sections(data, header_info):
    sections = []
    offset = header_info["section_table_offset"]
    for _ in range(header_info["number_of_sections"]):
        virtual_size = struct.unpack_from("<I", data, offset + 8)[0]
        virtual_address = struct.unpack_from("<I", data, offset + 12)[0]
        size_of_raw_data = struct.unpack_from("<I", data, offset + 16)[0]
        pointer_to_raw_data = struct.unpack_from("<I", data, offset + 20)[0]
        sections.append(
            {
                "virtual_address": virtual_address,
                "virtual_size": virtual_size,
                "size_of_raw_data": size_of_raw_data,
                "pointer_to_raw_data": pointer_to_raw_data,
            }
        )
        offset += 40
    return sections


def rva_to_offset(rva, sections):
    for section in sections:
        start = section["virtual_address"]
        size = max(section["virtual_size"], section["size_of_raw_data"])
        end = start + size
        if start <= rva < end:
            return section["pointer_to_raw_data"] + (rva - start)
    raise ValueError(f"RVA 0x{rva:X} not found in any section")


def read_debug_directory(data, header_info, sections):
    if header_info["number_of_rva_and_sizes"] <= 6:
        raise ValueError("PE file has no debug directory")

    debug_dir_offset = header_info["data_directories_offset"] + 6 * 8
    debug_rva, debug_size = struct.unpack_from("<II", data, debug_dir_offset)
    if debug_rva == 0 or debug_size == 0:
        raise ValueError("PE file has empty debug directory")

    file_offset = rva_to_offset(debug_rva, sections)
    entry_count = debug_size // 28
    for index in range(entry_count):
        entry_offset = file_offset + index * 28
        debug_type = struct.unpack_from("<I", data, entry_offset + 12)[0]
        size_of_data = struct.unpack_from("<I", data, entry_offset + 16)[0]
        address_of_raw_data = struct.unpack_from("<I", data, entry_offset + 20)[0]
        pointer_to_raw_data = struct.unpack_from("<I", data, entry_offset + 24)[0]
        if debug_type != 2:
            continue

        if pointer_to_raw_data != 0:
            return data[pointer_to_raw_data : pointer_to_raw_data + size_of_data]

        raw_offset = rva_to_offset(address_of_raw_data, sections)
        return data[raw_offset : raw_offset + size_of_data]

    raise ValueError("CodeView debug entry not found")


def parse_codeview_record(record):
    if record[:4] != b"RSDS":
        raise ValueError("unsupported CodeView record, expected RSDS")

    guid = uuid.UUID(bytes_le=record[4:20]).hex.upper()
    age = struct.unpack_from("<I", record, 20)[0]
    pdb_name = Path(
        record[24:].split(b"\0", 1)[0].decode("utf-8", errors="replace")
    ).name
    if not pdb_name:
        raise ValueError("missing pdb file name in CodeView record")

    return pdb_name, f"{guid}{age:X}"


def build_pdb_url(dll_path):
    data = dll_path.read_bytes()
    header_info = read_pe_headers(data)
    sections = read_sections(data, header_info)
    codeview_record = read_debug_directory(data, header_info, sections)
    pdb_name, pdb_id = parse_codeview_record(codeview_record)
    return f"{SYMSRV_BASE_URL}/{pdb_name}/{pdb_id}/{pdb_name}"


def extract_version(dll_path):
    suffix = "-chrome.dll"
    stem = dll_path.name
    if not stem.endswith(suffix):
        raise ValueError(f"cannot extract version from {dll_path.name}")

    prefix = stem[: -len(suffix)]
    version, sep, _arch = prefix.rpartition("-")
    if not sep or not version:
        raise ValueError(f"cannot extract version from {dll_path.name}")
    return version


def iter_default_dll_paths(binary_dir):
    return sorted(Path(binary_dir).glob("*chrome.dll"))


def verify_pdb_file(output_path):
    signature = b"Microsoft C/C++ MSF "
    with output_path.open("rb") as file_obj:
        header = file_obj.read(len(signature))
    if header != signature:
        raise RuntimeError("downloaded file is not a valid PDB")


def main():
    args = parse_args()
    dll_paths = args.dll_paths or iter_default_dll_paths(args.dll_dir)

    if not dll_paths:
        print(f"no files matched: {args.dll_dir}\\*chrome.dll", file=sys.stderr)
        return 1

    binary_dir = Path(args.binary_dir)
    targets = []

    try:
        for dll_path in dll_paths:
            version = extract_version(dll_path)
            url = build_pdb_url(dll_path)
            targets.append(
                DownloadTarget(
                    label=version,
                    url=url,
                    output_path=binary_dir / f"{version}-chrome.dll.pdb",
                )
            )
    except FileNotFoundError as exc:
        print(f"file not found: {exc.filename}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if not targets:
        print("No pdb urls found.")
        return 0

    if not prompt_for_download(targets, args.binary_dir):
        print("Canceled.")
        return 0

    try:
        download_targets(
            targets,
            args.proxy,
            args.timeout,
            verify_pdb_file,
            gzip_range=args.download_method == "gzip-range",
            gzip_range_workers=args.gzip_range_workers,
            gzip_range_chunk_size=args.gzip_range_chunk_size,
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"Downloaded {len(targets)} files to {args.binary_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
