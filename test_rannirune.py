import ctypes
import contextlib
import io
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import rannirune


def tpf(payload: bytes = b"DDS " + bytes(124)) -> bytes:
    header = bytearray(36)
    header[:4] = b"TPF\0"
    struct.pack_into("<I", header, 8, 1)
    struct.pack_into("<II", header, 16, len(header), len(payload))
    return bytes(header) + payload


class FormatTests(unittest.TestCase):
    def test_omitting_oodle_uses_elden_ring_steam_install(self):
        expected_library = (
            r"D:\Games\Steam\steamapps\common\ELDEN RING\Game\oo2core_6_win64.dll"
        )
        with patch.dict("os.environ", {"ProgramFiles(x86)": r"D:\Games"}, clear=True):
            with patch("sys.argv", ["rannirune.py", "input.tpf.dcx"]):
                with patch("rannirune.convert") as convert:
                    self.assertEqual(rannirune.main(), 0)
        convert.assert_called_once_with(
            Path("input.tpf.dcx"), Path("input.png"), expected_library
        )

    def test_matbinbnd_dcx_uses_declared_compressed_size(self):
        source = Path("samples/allmaterial.matbinbnd.dcx")
        data = source.read_bytes()
        compressed_size = struct.unpack_from(">I", data, 32)[0]
        self.assertEqual(len(data) - 76 - compressed_size, 9)

        class FakeDecompress:
            def __call__(self, source, source_size, destination, destination_size,
                         *args):
                self.source_size = source_size
                ctypes.memmove(destination, b"BND4" + bytes(destination_size - 4),
                               destination_size)
                return destination_size

        decompress = FakeDecompress()

        class FakeLibrary:
            OodleLZ_Decompress = decompress

        with patch("rannirune.ctypes.CDLL", return_value=FakeLibrary()):
            output = rannirune.decompress_dcx(data, "unused")

        self.assertEqual(decompress.source_size, compressed_size)
        self.assertEqual(output[:4], b"BND4")

    def test_decompresses_matbinbnd_to_bnd4_without_overwriting(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "allmaterial.matbinbnd.dcx"
            destination = Path(temp_dir) / "allmaterial.matbinbnd"
            source.write_bytes(b"compressed")
            with patch("rannirune.decompress_dcx", return_value=b"BND4payload"):
                rannirune.decompress_binder(source, destination, "unused")
            self.assertEqual(destination.read_bytes(), b"BND4payload")
            with patch("rannirune.decompress_dcx", return_value=b"BND4new"):
                with self.assertRaisesRegex(rannirune.FormatError, "already exists"):
                    rannirune.decompress_binder(source, destination, "unused")
            self.assertEqual(destination.read_bytes(), b"BND4payload")

    def test_existing_output_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "input.tpf.dcx"
            destination = Path(temp_dir) / "input.png"
            source.write_bytes(b"not decoded")
            destination.write_bytes(b"keep this")
            with self.assertRaisesRegex(rannirune.FormatError, "output already exists"):
                rannirune.convert(source, destination, "unused")
            self.assertEqual(destination.read_bytes(), b"keep this")

    def test_directory_processes_remaining_files_after_errors(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source_dir = Path(temp_dir) / "input"
            output_dir = Path(temp_dir) / "output"
            source_dir.mkdir()
            (source_dir / "a.tpf.dcx").write_bytes(b"")
            (source_dir / "b.tpf.dcx").write_bytes(b"")
            (source_dir / "notes.txt").write_text("unsupported")
            stdout = io.StringIO()
            stderr = io.StringIO()

            def convert(source, destination, library):
                if source.name == "a.tpf.dcx":
                    raise rannirune.FormatError("bad input")

            with patch("sys.argv", [
                "rannirune.py", str(source_dir), "-o", str(output_dir)
            ]):
                with patch("rannirune.convert", side_effect=convert) as convert_mock:
                    with contextlib.redirect_stdout(stdout):
                        with contextlib.redirect_stderr(stderr):
                            self.assertEqual(rannirune.main(), 1)

            self.assertEqual(convert_mock.call_count, 2)
            self.assertIn("bad input", stderr.getvalue())
            self.assertIn("unsupported file format", stderr.getvalue())
            self.assertIn(str(output_dir / "b.png"), stdout.getvalue())
            self.assertTrue(output_dir.is_dir())

    def test_extracts_dds_at_entry_offset(self):
        self.assertEqual(rannirune.extract_dds(tpf()), b"DDS " + bytes(124))

    def test_rejects_bad_texture_count_and_platform(self):
        for offset, value in ((8, 2), (12, 1)):
            data = bytearray(tpf())
            data[offset] = value
            with self.subTest(offset=offset), self.assertRaises(rannirune.FormatError):
                rannirune.extract_dds(bytes(data))

    def test_rejects_out_of_bounds_or_wrong_magic(self):
        data = bytearray(tpf())
        struct.pack_into("<I", data, 16, len(data))
        with self.assertRaises(rannirune.FormatError):
            rannirune.extract_dds(bytes(data))
        with self.assertRaises(rannirune.FormatError):
            rannirune.extract_dds(tpf(b"BAD " + bytes(124)))

    def test_rejects_truncated_dcx_before_loading_library(self):
        with self.assertRaises(rannirune.FormatError):
            rannirune.decompress_dcx(b"DCX\0", "not-a-library")


if __name__ == "__main__":
    unittest.main()
