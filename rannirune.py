#!/usr/bin/env python3
"""Convert a KRAK-compressed PC TPF texture to PNG."""

import argparse
import ctypes
import io
import os
from pathlib import Path
import struct
import sys

from PIL import Image, UnidentifiedImageError


MAX_DECOMPRESSED_SIZE = 512 * 1024 * 1024


def default_oodle_library() -> str:
    install_root = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    return str(
        Path(install_root)
        / "Steam"
        / "steamapps"
        / "common"
        / "ELDEN RING"
        / "Game"
        / "oo2core_6_win64.dll"
    )


class FormatError(ValueError):
    """The input is invalid or uses an unsupported format."""


def decompress_dcx(data: bytes, library: str) -> bytes:
    if len(data) < 76:
        raise FormatError("DCX header is truncated")
    if (data[:4] != b"DCX\0" or data[24:28] != b"DCS\0"
            or data[36:44] != b"DCP\0KRAK" or data[68:72] != b"DCA\0"):
        raise FormatError("expected a DCX DCP/KRAK container")
    if (struct.unpack_from(">5I", data, 4) != (0x11000, 0x18, 0x24, 0x44, 0x4c)
            or struct.unpack_from(">I", data, 44)[0] != 0x20
            or struct.unpack_from(">I", data, 72)[0] != 8):
        raise FormatError("unsupported DCX header layout")
    raw_size, compressed_size = struct.unpack_from(">II", data, 28)
    if raw_size == 0 or raw_size > MAX_DECOMPRESSED_SIZE:
        raise FormatError("invalid or excessive DCX uncompressed size")
    if compressed_size == 0 or compressed_size > len(data) - 76:
        raise FormatError("DCX compressed size exceeds the data in the file")

    try:
        dll = ctypes.CDLL(library)
    except OSError as exc:
        raise FormatError(f"cannot load Oodle library {library!r}: {exc}") from exc
    try:
        decompress = dll.OodleLZ_Decompress
    except AttributeError as exc:
        raise FormatError("Oodle library does not export OodleLZ_Decompress") from exc
    decompress.argtypes = [
        ctypes.c_void_p, ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_ssize_t,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_void_p,
        ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_ssize_t, ctypes.c_int,
    ]
    decompress.restype = ctypes.c_ssize_t
    source = ctypes.create_string_buffer(data[76:])
    output = ctypes.create_string_buffer(raw_size)
    result = decompress(source, compressed_size, output, raw_size, 1, 0, 0,
                        None, 0, None, None, None, 0, 3)
    if result != raw_size:
        raise FormatError(f"Oodle decompression failed: decoded {result} of {raw_size} bytes")
    return output.raw


def extract_dds(data: bytes) -> bytes:
    if len(data) < 36 or data[:4] != b"TPF\0":
        raise FormatError("expected a PC TPF header and texture entry")
    texture_count = struct.unpack_from("<I", data, 8)[0]
    if data[12] != 0:
        raise FormatError(f"unsupported TPF platform {data[12]}; expected PC")
    if texture_count != 1:
        raise FormatError(f"expected exactly one TPF texture, found {texture_count}")
    offset, size = struct.unpack_from("<II", data, 16)
    if offset < 36 or size < 128 or offset + size > len(data):
        raise FormatError("TPF texture offset or size is invalid")
    dds = data[offset:offset + size]
    if dds[:4] != b"DDS ":
        raise FormatError("TPF texture is not a DDS image")
    return dds


def convert(source: Path, destination: Path, library: str) -> None:
    if source.resolve() == destination.resolve():
        raise FormatError("input and output must be different files")
    if destination.exists():
        raise FormatError(f"output already exists; skipping: {destination}")
    dds = extract_dds(decompress_dcx(source.read_bytes(), library))
    try:
        with Image.open(io.BytesIO(dds)) as image:
            if image.format != "DDS":
                raise FormatError("embedded texture is not a DDS image")
            output = io.BytesIO()
            image.convert("RGBA").save(output, format="PNG")
        with destination.open("xb") as file:
            file.write(output.getvalue())
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        if isinstance(exc, FormatError):
            raise
        if isinstance(exc, FileExistsError):
            raise FormatError(
                f"output already exists; skipping: {destination}"
            ) from exc
        raise FormatError(f"cannot decode DDS or write PNG: {exc}") from exc


def decompress_binder(source: Path, destination: Path, library: str) -> None:
    if source.resolve() == destination.resolve():
        raise FormatError("input and output must be different files")
    if destination.exists():
        raise FormatError(f"output already exists; skipping: {destination}")
    binder = decompress_dcx(source.read_bytes(), library)
    if not binder.startswith(b"BND4"):
        raise FormatError("decompressed file is not a BND4 binder")
    try:
        with destination.open("xb") as file:
            file.write(binder)
    except FileExistsError as exc:
        raise FormatError(
            f"output already exists; skipping: {destination}"
        ) from exc


def convert_directory(source_dir: Path, output_dir: Path, library: str) -> int:
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        sources = sorted(path for path in source_dir.iterdir() if path.is_file())
    except OSError as exc:
        print(f"rannirune: {exc}", file=sys.stderr)
        return 1

    failed = False
    for source in sources:
        lower_name = source.name.lower()
        if lower_name.endswith(".tpf.dcx"):
            destination = output_dir / (source.name[:-8] + ".png")
            convert_file = convert
        elif lower_name.endswith(".matbinbnd.dcx"):
            destination = output_dir / source.name[:-4]
            convert_file = decompress_binder
        else:
            print(f"rannirune: {source}: unsupported file format; skipping", file=sys.stderr)
            failed = True
            continue
        try:
            convert_file(source, destination, library)
        except (OSError, FormatError) as exc:
            print(f"rannirune: {source}: {exc}", file=sys.stderr)
            failed = True
            continue
        print(f"Wrote {destination}")
    return int(failed)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="input .tpf.dcx file or directory of files")
    parser.add_argument("-o", "--output", type=Path,
                        help="output file, or output directory when input is a directory")
    parser.add_argument("--oodle", default=os.environ.get("OODLE_LIBRARY", default_oodle_library()),
                        help="path to the Oodle library (or set OODLE_LIBRARY)")
    args = parser.parse_args()
    if args.input.is_dir():
        return convert_directory(args.input, args.output or args.input, args.oodle)
    name = args.input.name
    lower_name = name.lower()
    if lower_name.endswith(".tpf.dcx"):
        output = args.output or args.input.with_name(name[:-8] + ".png")
        convert_file = convert
    elif lower_name.endswith(".matbinbnd.dcx"):
        output = args.output or args.input.with_name(name[:-4])
        convert_file = decompress_binder
    else:
        parser.error("input must be a .tpf.dcx or .matbinbnd.dcx file")
    try:
        convert_file(args.input, output, args.oodle)
    except (OSError, FormatError) as exc:
        print(f"rannirune: {exc}", file=sys.stderr)
        return 1
    print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
