"""バックアップ用のフラッシュ解析。実機なしでも検証できるよう独立させています。

`scripts/backup-device.sh` から呼び出します。読み取り専用で、書き込みは一切しません。
"""

import argparse
import re
import struct
import sys


PARTITION_MAGIC = b"\xaa\x50"
PARTITION_ENTRY_BYTES = 32
PARTITION_TABLE_BYTES = 0xC00
PARTITION_TABLE_OFFSETS = (0x8000, 0x9000, 0xF000)
APP_DESC_MAGIC = 0xABCD5432
APP_DESC_OFFSET = 0x20
APP_DESC_BYTES = 256
PARTITION_TYPES = {0: "app", 1: "data"}
UNITS = {"kb": 1024, "mb": 1024**2, "gb": 1024**3}


class FlashError(ValueError):
    pass


def parse_flash_size(label: str) -> int:
    """esptool の `Detected flash size:` の表記をバイト数にします。"""
    match = re.fullmatch(r"(\d+)\s*(KB|MB|GB)", label.strip(), re.IGNORECASE)
    if not match:
        raise FlashError(f"フラッシュ容量の表記を解釈できません: {label}")
    return int(match.group(1)) * UNITS[match.group(2).lower()]


def find_partition_table_offset(image: bytes) -> int:
    for offset in PARTITION_TABLE_OFFSETS:
        if image[offset : offset + 2] == PARTITION_MAGIC:
            return offset
    raise FlashError("パーティションテーブルの位置を特定できません。")


def iter_partitions(table: bytes):
    for index in range(0, len(table), PARTITION_ENTRY_BYTES):
        entry = table[index : index + PARTITION_ENTRY_BYTES]
        if len(entry) < PARTITION_ENTRY_BYTES or entry[:2] != PARTITION_MAGIC:
            return
        kind, subtype = entry[2], entry[3]
        offset, size = struct.unpack("<II", entry[4:12])
        label = _decode(entry[12:28])
        yield label, kind, subtype, offset, size


def format_partition_table(table: bytes, table_offset: int) -> str:
    lines = [f"# パーティションテーブル offset=0x{table_offset:x}", "# name, type, subtype, offset, size"]
    for label, kind, subtype, offset, size in iter_partitions(table):
        lines.append(
            f"{label}, {PARTITION_TYPES.get(kind, kind)}, 0x{subtype:02x}, 0x{offset:x}, 0x{size:x}"
        )
    return "\n".join(lines)


def describe_app_partitions(full: bytes, table: bytes) -> str:
    lines: list[str] = []
    found = False
    for label, kind, _subtype, offset, size in iter_partitions(table):
        if kind != 0:
            continue
        header = full[offset + APP_DESC_OFFSET : offset + APP_DESC_OFFSET + APP_DESC_BYTES]
        if len(header) < APP_DESC_BYTES or struct.unpack("<I", header[0:4])[0] != APP_DESC_MAGIC:
            lines.append(f"[{label}] アプリ記述子が見つかりません（offset=0x{offset:x}）。")
            continue
        found = True
        lines.append(f"[{label}] offset=0x{offset:x} size=0x{size:x}")
        lines.append(f"  project_name: {_decode(header[48:80])}")
        lines.append(f"  version:      {_decode(header[16:48])}")
        lines.append(f"  compile_time: {_decode(header[80:96])} {_decode(header[96:112])}")
        lines.append(f"  idf_version:  {_decode(header[112:144])}")
    if not found:
        lines.append("アプリ記述子を読み取れませんでした。full-flash.bin は取得済みです。")
    return "\n".join(lines)


def _decode(raw: bytes) -> str:
    return raw.split(b"\x00", 1)[0].decode("utf-8", "replace")


def _read(path: str) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


def main() -> int:
    parser = argparse.ArgumentParser(description="バックアップ用のフラッシュ解析です。")
    sub = parser.add_subparsers(dest="command", required=True)

    flash_size = sub.add_parser("flash-size", help="容量表記をバイト数にします。")
    flash_size.add_argument("label")

    table_offset = sub.add_parser("table-offset", help="パーティションテーブルの位置を探します。")
    table_offset.add_argument("full_image")

    describe = sub.add_parser("describe-table", help="パーティションテーブルを展開します。")
    describe.add_argument("full_image")
    describe.add_argument("table_image")
    describe.add_argument("table_offset")

    app_info = sub.add_parser("app-info", help="アプリのバージョン文字列を読み取ります。")
    app_info.add_argument("full_image")
    app_info.add_argument("table_image")

    args = parser.parse_args()
    try:
        if args.command == "flash-size":
            print(parse_flash_size(args.label))
        elif args.command == "table-offset":
            print(hex(find_partition_table_offset(_read(args.full_image))))
        elif args.command == "describe-table":
            full = _read(args.full_image)
            table = _read(args.table_image)
            offset = int(args.table_offset, 16)
            if table != full[offset : offset + len(table)]:
                raise FlashError("パーティションテーブルの個別読み出しが全体ダンプと一致しません。")
            print(format_partition_table(table, offset))
        elif args.command == "app-info":
            print(describe_app_partitions(_read(args.full_image), _read(args.table_image)))
    except FlashError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
