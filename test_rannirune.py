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


def multi_tpf(payloads: list[bytes]) -> bytes:
    header = bytearray(16 + 20 * len(payloads))
    header[:4] = b"TPF\0"
    struct.pack_into("<I", header, 8, len(payloads))
    offset = len(header)
    for index, payload in enumerate(payloads):
        struct.pack_into("<II", header, 16 + index * 20, offset, len(payload))
        offset += len(payload)
    return bytes(header) + b"".join(payloads)


def dds_image() -> bytes:
    output = io.BytesIO()
    rannirune.Image.new("RGBA", (4, 4), (255, 0, 0, 255)).save(output, format="DDS")
    return output.getvalue()


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

    def test_single_partsbnd_input_uses_binder_output_name(self):
        source = Path("fc_f_0000.partsbnd.dcx")
        destination = Path("fc_f_0000.partsbnd")
        with patch("sys.argv", ["rannirune.py", str(source)]):
            with patch("rannirune.decompress_binder") as decompress_binder:
                self.assertEqual(rannirune.main(), 0)
        decompress_binder.assert_called_once_with(
            source, destination, rannirune.os.environ.get(
                "OODLE_LIBRARY", rannirune.default_oodle_library()
            )
        )

    def test_directory_processes_partsbnd_as_binder(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source_dir = Path(temp_dir) / "input"
            output_dir = Path(temp_dir) / "output"
            source_dir.mkdir()
            source = source_dir / "fc_f_0000.partsbnd.dcx"
            source.write_bytes(b"compressed")
            with patch("rannirune.decompress_binder") as decompress_binder:
                self.assertEqual(
                    rannirune.convert_directory(source_dir, output_dir, "unused"), 0
                )
            decompress_binder.assert_called_once_with(
                source, output_dir / "fc_f_0000.partsbnd", "unused"
            )

    def test_existing_output_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "input.tpf.dcx"
            destination = Path(temp_dir) / "input.png"
            source.write_bytes(b"not decoded")
            destination.write_bytes(b"keep this")
            with patch("rannirune.decompress_dcx", return_value=tpf(dds_image())):
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
                print(f"Wrote {destination}")

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

    def test_recursive_directory_processes_files_before_children_after_errors(self):
        temp_root = Path(__file__).parent / "tmp"
        temp_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as temp_dir:
            source_dir = Path(temp_dir) / "input"
            output_dir = Path(temp_dir) / "output"
            (source_dir / "a" / "nested").mkdir(parents=True)
            (source_dir / "b").mkdir()
            relative_sources = [
                Path("z.tpf.dcx"),
                Path("a") / "a.tpf.dcx",
                Path("a") / "nested" / "binder.matbinbnd.dcx",
                Path("b") / "b.tpf.dcx",
            ]
            for relative in relative_sources:
                (source_dir / relative).write_bytes(b"input")
            processed = []

            def convert(source, destination, library):
                processed.append(source.relative_to(source_dir))
                if source.name == "a.tpf.dcx":
                    raise rannirune.FormatError("bad child input")
                self.assertTrue(destination.parent.is_dir())

            stderr = io.StringIO()
            with patch("rannirune.convert", side_effect=convert):
                with patch("rannirune.decompress_binder", side_effect=convert):
                    with contextlib.redirect_stdout(io.StringIO()):
                        with contextlib.redirect_stderr(stderr):
                            self.assertEqual(
                                rannirune.convert_directory(source_dir, output_dir, "unused"),
                                1,
                            )
            self.assertEqual(processed, relative_sources)
            self.assertIn("bad child input", stderr.getvalue())
            self.assertTrue((output_dir / "a" / "nested").is_dir())
            self.assertTrue((output_dir / "b").is_dir())

    def test_recursive_directory_excludes_output_inside_input_tree(self):
        temp_root = Path(__file__).parent / "tmp"
        temp_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as temp_dir:
            source_dir = Path(temp_dir)
            output_dir = source_dir / "child" / "output"
            output_dir.mkdir(parents=True)
            source = source_dir / "child" / "input.tpf.dcx"
            source.write_bytes(b"input")
            (output_dir / "ignore.tpf.dcx").write_bytes(b"already in output")
            with patch("rannirune.convert") as convert:
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(
                        rannirune.convert_directory(source_dir, output_dir, "unused"), 0
                    )
            convert.assert_called_once_with(
                source, output_dir / "child" / "input.png", "unused"
            )

    def test_recursive_in_place_binder_preserves_existing_outputs(self):
        temp_root = Path(__file__).parent / "tmp"
        temp_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as temp_dir:
            source_dir = Path(temp_dir)
            child = source_dir / "child"
            child.mkdir()
            (child / "a.matbinbnd.dcx").write_bytes(b"compressed")
            (child / "a.matbinbnd").write_bytes(b"keep this")
            (child / "b.matbinbnd.dcx").write_bytes(b"compressed")
            stderr = io.StringIO()
            with patch("rannirune.decompress_dcx", return_value=b"BND4payload"):
                with contextlib.redirect_stdout(io.StringIO()):
                    with contextlib.redirect_stderr(stderr):
                        self.assertEqual(
                            rannirune.convert_directory(source_dir, source_dir, "unused"), 1
                        )
            self.assertEqual((child / "a.matbinbnd").read_bytes(), b"keep this")
            self.assertEqual((child / "b.matbinbnd").read_bytes(), b"BND4payload")
            self.assertIn("output already exists", stderr.getvalue())

    def test_o2_flattens_outputs_and_continues_after_name_collision(self):
        temp_root = Path(__file__).parent / "tmp"
        temp_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as temp_dir:
            source_dir = Path(temp_dir)
            output_dir = source_dir / "output"
            (source_dir / "child").mkdir()
            (source_dir / "same.matbinbnd.dcx").write_bytes(b"root")
            (source_dir / "child" / "same.matbinbnd.dcx").write_bytes(b"duplicate")
            (source_dir / "child" / "unique.tpf.dcx").write_bytes(b"texture")
            stderr = io.StringIO()
            with patch("sys.argv", ["rannirune.py", str(source_dir),
                                    "-o2", str(output_dir)]):
                with patch("rannirune.decompress_dcx", return_value=b"BND4root"):
                    with patch("rannirune.convert") as convert:
                        with contextlib.redirect_stdout(io.StringIO()):
                            with contextlib.redirect_stderr(stderr):
                                self.assertEqual(rannirune.main(), 1)
            self.assertEqual((output_dir / "same.matbinbnd").read_bytes(), b"BND4root")
            convert.assert_called_once_with(
                source_dir / "child" / "unique.tpf.dcx",
                output_dir / "unique.png",
                rannirune.os.environ.get("OODLE_LIBRARY", rannirune.default_oodle_library()),
            )
            self.assertFalse((output_dir / "child").exists())
            self.assertIn("output already exists", stderr.getvalue())

    def test_o2_single_file_creates_destination_directory(self):
        temp_root = Path(__file__).parent / "tmp"
        temp_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as temp_dir:
            source = Path(temp_dir) / "input.tpf.dcx"
            output_dir = Path(temp_dir) / "output"
            with patch("sys.argv", ["rannirune.py", str(source), "-o2", str(output_dir)]):
                with patch("rannirune.convert") as convert:
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(rannirune.main(), 0)
            self.assertEqual(convert.call_args.args[:2], (source, output_dir / "input.png"))
            self.assertTrue(output_dir.is_dir())

    def test_o_and_o2_are_mutually_exclusive(self):
        with patch("sys.argv", ["rannirune.py", "input.tpf.dcx",
                                "-o", "output.png", "-o2", "output"]):
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    rannirune.main()
        self.assertEqual(error.exception.code, 2)

    def test_extracts_dds_at_entry_offset(self):
        self.assertEqual(rannirune.extract_dds(tpf()), b"DDS " + bytes(124))

    def test_rejects_bad_texture_count_and_platform(self):
        for offset, value in ((8, 0), (12, 1)):
            data = bytearray(tpf())
            data[offset] = value
            with self.subTest(offset=offset), self.assertRaises(rannirune.FormatError):
                rannirune.extract_dds(bytes(data))

    def test_exports_multiple_textures_with_indices_and_preserves_existing_output(self):
        temp_root = Path(__file__).parent / "tmp"
        temp_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as temp_dir:
            source = Path(temp_dir) / "input.tpf.dcx"
            destination = Path(temp_dir) / "custom.png"
            source.write_bytes(b"compressed")
            existing = Path(temp_dir) / "custom_1.png"
            existing.write_bytes(b"keep this")
            data = multi_tpf([dds_image()] * 3)
            with patch("rannirune.decompress_dcx", return_value=data):
                with contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaisesRegex(rannirune.FormatError, "texture 1"):
                        rannirune.convert(source, destination, "unused")
            for index in (0, 2):
                with rannirune.Image.open(Path(temp_dir) / f"custom_{index}.png") as image:
                    self.assertEqual(image.size, (4, 4))
                    self.assertEqual(image.getpixel((0, 0)), (255, 0, 0, 255))
            self.assertEqual(existing.read_bytes(), b"keep this")
            self.assertFalse(destination.exists())

    def test_unsupported_dds_and_invalid_entries_do_not_stop_other_textures(self):
        temp_root = Path(__file__).parent / "tmp"
        temp_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as temp_dir:
            source_dir = Path(temp_dir) / "input"
            output_dir = Path(temp_dir) / "output"
            source_dir.mkdir()
            (source_dir / "a.tpf.dcx").write_bytes(b"input")
            (source_dir / "b.tpf.dcx").write_bytes(b"input")
            valid = dds_image()
            data = multi_tpf([b"BAD " + bytes(124), valid, valid])
            stderr = io.StringIO()
            real_open = rannirune.Image.open
            calls = 0

            def open_image(*args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise NotImplementedError("Unimplemented DXGI format 999")
                return real_open(*args, **kwargs)

            with patch("rannirune.decompress_dcx", side_effect=[data, tpf(valid)]):
                with patch("rannirune.Image.open", side_effect=open_image):
                    with contextlib.redirect_stdout(io.StringIO()):
                        with contextlib.redirect_stderr(stderr):
                            self.assertEqual(
                                rannirune.convert_directory(source_dir, output_dir, "unused"),
                                1,
                            )
            self.assertTrue((output_dir / "a_2.png").is_file())
            self.assertTrue((output_dir / "b.png").is_file())
            self.assertIn("texture 0", stderr.getvalue())
            self.assertIn("Unimplemented DXGI format 999", stderr.getvalue())

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
