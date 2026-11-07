import struct
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
