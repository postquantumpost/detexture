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


def texture_count(data: bytes) -> int:
    if len(data) < 16 or data[:4] != b"TPF\0":
        raise FormatError("expected a PC TPF header and texture entry")
    count = struct.unpack_from("<I", data, 8)[0]
    if data[12] != 0:
        raise FormatError(f"unsupported TPF platform {data[12]}; expected PC")
    if count == 0 or 16 + count * 20 > len(data):
        raise FormatError("invalid TPF texture count or truncated entry table")
    return count


def extract_dds(data: bytes, index: int = 0) -> bytes:
    count = texture_count(data)
    if not 0 <= index < count:
        raise FormatError(f"TPF texture index {index} is out of range")
    entry = 16 + index * 20
    if data[entry + 19] != 0:
        raise FormatError(f"unsupported TPF texture flags {data[entry + 19]}")
    offset, size = struct.unpack_from("<II", data, entry)
    if offset < 16 + count * 20 or size < 128 or offset + size > len(data):
        raise FormatError("TPF texture offset or size is invalid")
    dds = data[offset:offset + size]
    if dds[:4] != b"DDS ":
        raise FormatError("TPF texture is not a DDS image")
    return dds


def write_png(dds: bytes, destination: Path) -> None:
    if destination.exists():
        raise FormatError(f"output already exists; skipping: {destination}")
    if len(dds) >= 148 and dds[84:88] == b"DX10":
        dxgi_format = struct.unpack_from("<I", dds, 128)[0]
        if dxgi_format == 72:
            # BC1 sRGB has identical block encoding to BC1 UNORM.
            header = bytearray(dds[:148])
            struct.pack_into("<I", header, 128, 71)
            dds = bytes(header) + dds[148:]
    try:
        with Image.open(io.BytesIO(dds)) as image:
            if image.format != "DDS":
                raise FormatError("embedded texture is not a DDS image")
            output = io.BytesIO()
            image.convert("RGBA").save(output, format="PNG")
        with destination.open("xb") as file:
            file.write(output.getvalue())
    except (UnidentifiedImageError, OSError, ValueError, NotImplementedError) as exc:
        if isinstance(exc, FormatError):
            raise
        if isinstance(exc, FileExistsError):
            raise FormatError(
                f"output already exists; skipping: {destination}"
            ) from exc
        raise FormatError(f"cannot decode DDS or write PNG: {exc}") from exc


def convert(source: Path, destination: Path, library: str) -> None:
    if source.resolve() == destination.resolve():
        raise FormatError("input and output must be different files")
    data = decompress_dcx(source.read_bytes(), library)
    count = texture_count(data)
    errors = []
    for index in range(count):
        output = (destination if count == 1 else
                  destination.with_name(f"{destination.stem}_{index}.png"))
        try:
            if source.resolve() == output.resolve():
                raise FormatError("input and output must be different files")
            write_png(extract_dds(data, index), output)
        except (OSError, FormatError) as exc:
            errors.append(f"texture {index} ({output}): {exc}")
            continue
        print(f"Wrote {output}")
    if errors:
        raise FormatError("\n".join(errors))


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


def convert_directory(source_dir: Path, output_dir: Path, library: str,
                      flat: bool = False) -> int:
    failed = False

    def report_error(exc: OSError) -> None:
        nonlocal failed
        print(f"rannirune: {exc}", file=sys.stderr)
        failed = True

    try:
        output_root = output_dir.resolve()
        input_root = source_dir.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        report_error(exc)
        return 1

    visited = set()
    for directory, children, filenames in os.walk(source_dir, onerror=report_error):
        current = Path(directory)
        try:
            resolved = current.resolve()
            if resolved in visited:
                children.clear()
                print(f"rannirune: {current}: directory already visited; skipping",
                      file=sys.stderr)
                failed = True
                continue
            visited.add(resolved)
            children[:] = sorted(
                name for name in children
                if output_root == input_root or (current / name).resolve() != output_root
            )
            destination_dir = output_dir if flat else output_dir / current.relative_to(source_dir)
            destination_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            report_error(exc)
            children.clear()
            continue

        for name in sorted(filenames):
            source = current / name
            lower_name = name.lower()
            if lower_name.endswith(".tpf.dcx"):
                destination = destination_dir / (name[:-8] + ".png")
                convert_file = convert
            elif lower_name.endswith(".matbinbnd.dcx"):
                destination = destination_dir / name[:-4]
                convert_file = decompress_binder
            else:
                print(f"rannirune: {source}: unsupported file format; skipping",
                      file=sys.stderr)
                failed = True
                continue
            try:
                convert_file(source, destination, library)
            except (OSError, FormatError) as exc:
                print(f"rannirune: {source}: {exc}", file=sys.stderr)
                failed = True
                continue
            if convert_file is decompress_binder:
                print(f"Wrote {destination}")
    return int(failed)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path,
                        help="input .tpf.dcx file or directory (processed recursively)")
    outputs = parser.add_mutually_exclusive_group()
    outputs.add_argument("-o", "--output", type=Path,
                         help="output file, or output directory preserving subdirectories")
    outputs.add_argument("-o2", type=Path, metavar="DIRECTORY",
                         help="output directory without preserving subdirectories")
    parser.add_argument("--oodle", default=os.environ.get("OODLE_LIBRARY", default_oodle_library()),
                        help="path to the Oodle library (or set OODLE_LIBRARY)")
    args = parser.parse_args()
    if args.input.is_dir():
        if args.o2 is not None:
            return convert_directory(args.input, args.o2, args.oodle, flat=True)
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
    if args.o2 is not None:
        output = args.o2 / output.name
    try:
        if args.o2 is not None:
            args.o2.mkdir(parents=True, exist_ok=True)
        convert_file(args.input, output, args.oodle)
    except (OSError, FormatError) as exc:
        print(f"rannirune: {exc}", file=sys.stderr)
        return 1
    if convert_file is decompress_binder:
        print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
