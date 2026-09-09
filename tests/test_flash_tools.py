"""`scripts/backup-device.sh` が依存する解析を実機なしで検証します。"""

import importlib.util
from pathlib import Path
import struct
import unittest


ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("flash_tools", ROOT / "scripts" / "flash_tools.py")
flash_tools = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(flash_tools)


TABLE_OFFSET = 0x8000
APP_OFFSET = 0x10000


def partition_entry(label: str, kind: int, subtype: int, offset: int, size: int) -> bytes:
    return (
        b"\xaa\x50"
        + bytes([kind, subtype])
        + struct.pack("<II", offset, size)
        + label.encode("utf-8").ljust(16, b"\x00")
        + struct.pack("<I", 0)
    )


def app_descriptor(version: str, project: str, idf: str) -> bytes:
    descriptor = bytearray(256)
    struct.pack_into("<I", descriptor, 0, flash_tools.APP_DESC_MAGIC)
    descriptor[16:48] = version.encode("utf-8").ljust(32, b"\x00")
    descriptor[48:80] = project.encode("utf-8").ljust(32, b"\x00")
    descriptor[80:96] = b"10:00:00".ljust(16, b"\x00")
    descriptor[96:112] = b"Sep  9 2026".ljust(16, b"\x00")
    descriptor[112:144] = idf.encode("utf-8").ljust(32, b"\x00")
    return bytes(descriptor)


def synthetic_flash() -> tuple[bytes, bytes]:
    image = bytearray(4 * 1024 * 1024)
    table = bytearray(flash_tools.PARTITION_TABLE_BYTES)
    entries = (
        partition_entry("nvs", 1, 0x02, 0x9000, 0x5000)
        + partition_entry("factory", 0, 0x00, APP_OFFSET, 0x300000)
    )
    table[: len(entries)] = entries
    image[TABLE_OFFSET : TABLE_OFFSET + len(table)] = table
    descriptor = app_descriptor("1.4.2", "StackChan", "v5.1.2")
    image[APP_OFFSET + 0x20 : APP_OFFSET + 0x20 + len(descriptor)] = descriptor
    return bytes(image), bytes(table)


class ParseFlashSizeTests(unittest.TestCase):
    def test_reads_common_labels(self) -> None:
        self.assertEqual(flash_tools.parse_flash_size("4MB"), 4 * 1024 * 1024)
        self.assertEqual(flash_tools.parse_flash_size("16MB"), 16 * 1024 * 1024)
        self.assertEqual(flash_tools.parse_flash_size(" 512KB "), 512 * 1024)

    def test_rejects_unknown_label(self) -> None:
        for label in ("", "unknown", "16", "MB", "16 mib"):
            with self.subTest(label=label), self.assertRaises(flash_tools.FlashError):
                flash_tools.parse_flash_size(label)


class PartitionTableTests(unittest.TestCase):
    def test_finds_table_offset(self) -> None:
        image, _ = synthetic_flash()

        self.assertEqual(flash_tools.find_partition_table_offset(image), TABLE_OFFSET)

    def test_missing_table_is_an_error(self) -> None:
        with self.assertRaises(flash_tools.FlashError):
            flash_tools.find_partition_table_offset(bytes(1024 * 1024))

    def test_formats_entries(self) -> None:
        _, table = synthetic_flash()

        rendered = flash_tools.format_partition_table(table, TABLE_OFFSET)

        self.assertIn("nvs, data, 0x02, 0x9000, 0x5000", rendered)
        self.assertIn("factory, app, 0x00, 0x10000, 0x300000", rendered)

    def test_stops_at_first_unused_entry(self) -> None:
        _, table = synthetic_flash()

        self.assertEqual(len(list(flash_tools.iter_partitions(table))), 2)


class AppDescriptorTests(unittest.TestCase):
    def test_reads_firmware_version_strings(self) -> None:
        image, table = synthetic_flash()

        described = flash_tools.describe_app_partitions(image, table)

        self.assertIn("project_name: StackChan", described)
        self.assertIn("version:      1.4.2", described)
        self.assertIn("idf_version:  v5.1.2", described)

    def test_reports_missing_descriptor_without_failing(self) -> None:
        image, table = synthetic_flash()
        blanked = bytearray(image)
        blanked[APP_OFFSET + 0x20 : APP_OFFSET + 0x24] = b"\x00\x00\x00\x00"

        described = flash_tools.describe_app_partitions(bytes(blanked), table)

        self.assertIn("アプリ記述子が見つかりません", described)


if __name__ == "__main__":
    unittest.main()
