#!/usr/bin/env python3
import argparse
import ctypes
import sys
from pathlib import Path


class VS_FIXEDFILEINFO(ctypes.Structure):
    _fields_ = [
        ("dwSignature", ctypes.c_uint32),
        ("dwStrucVersion", ctypes.c_uint32),
        ("dwFileVersionMS", ctypes.c_uint32),
        ("dwFileVersionLS", ctypes.c_uint32),
        ("dwProductVersionMS", ctypes.c_uint32),
        ("dwProductVersionLS", ctypes.c_uint32),
        ("dwFileFlagsMask", ctypes.c_uint32),
        ("dwFileFlags", ctypes.c_uint32),
        ("dwFileOS", ctypes.c_uint32),
        ("dwFileType", ctypes.c_uint32),
        ("dwFileSubtype", ctypes.c_uint32),
        ("dwFileDateMS", ctypes.c_uint32),
        ("dwFileDateLS", ctypes.c_uint32),
    ]


def parse_args():
    parser = argparse.ArgumentParser(
        description="Collect DLL file versions and keep the local versions file sorted."
    )
    parser.add_argument(
        "--input-dir",
        default="binary/dll",
        help="Directory containing DLL files, relative to the current directory",
    )
    parser.add_argument(
        "--output",
        default="versions",
        help="Output file path, relative to the current directory",
    )
    return parser.parse_args()


def read_file_version(dll_path):
    version = ctypes.windll.version
    size = version.GetFileVersionInfoSizeW(str(dll_path), None)
    if size == 0:
        raise OSError(f"failed to read version info size from {dll_path}")

    buffer = ctypes.create_string_buffer(size)
    if not version.GetFileVersionInfoW(str(dll_path), 0, size, buffer):
        raise OSError(f"failed to read version info from {dll_path}")

    value_ptr = ctypes.c_void_p()
    value_len = ctypes.c_uint()
    if not version.VerQueryValueW(buffer, "\\", ctypes.byref(value_ptr), ctypes.byref(value_len)):
        raise OSError(f"failed to query fixed version block from {dll_path}")

    fixed_info = ctypes.cast(value_ptr, ctypes.POINTER(VS_FIXEDFILEINFO)).contents
    if fixed_info.dwSignature != 0xFEEF04BD:
        raise OSError(f"unexpected version signature in {dll_path}")

    return ".".join(
        str(part)
        for part in (
            fixed_info.dwFileVersionMS >> 16,
            fixed_info.dwFileVersionMS & 0xFFFF,
            fixed_info.dwFileVersionLS >> 16,
            fixed_info.dwFileVersionLS & 0xFFFF,
        )
    )


def iter_dll_versions(input_dir):
    for dll_path in sorted(input_dir.glob("*.dll")):
        yield dll_path, read_file_version(dll_path)


def version_key(version):
    return tuple(int(part) for part in version.split("."))


def read_existing_versions(output_path):
    if not output_path.exists():
        return []

    with output_path.open("r", encoding="utf-8") as output_file:
        return [line.strip() for line in output_file if line.strip()]


def main():
    args = parse_args()
    base_dir = Path.cwd()
    input_dir = base_dir / args.input_dir
    output_path = base_dir / args.output

    if not input_dir.is_dir():
        raise SystemExit(f"input directory not found: {input_dir}")

    entries = list(iter_dll_versions(input_dir))
    if not entries:
        raise SystemExit(f"no dll files found in: {input_dir}")

    versions = read_existing_versions(output_path)
    versions.extend(version for _, version in entries)
    versions = sorted(set(versions), key=version_key)

    with output_path.open("w", encoding="utf-8", newline="") as output_file:
        for version in versions:
            output_file.write(f"{version}\r\n")

    print(f"wrote {len(versions)} sorted version entries to {output_path}")


if __name__ == "__main__":
    try:
        main()
    except OSError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1) from exc
