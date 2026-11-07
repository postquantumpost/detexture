import struct
import unittest

import rannirune


def tpf(payload: bytes = b"DDS " + bytes(124)) -> bytes:
    header = bytearray(36)
    header[:4] = b"TPF\0"
    struct.pack_into("<I", header, 8, 1)
    struct.pack_into("<II", header, 16, len(header), len(payload))
    return bytes(header) + payload


class FormatTests(unittest.TestCase):
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
