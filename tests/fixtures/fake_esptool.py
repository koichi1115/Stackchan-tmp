#!/usr/bin/env python3
"""実機なしで `scripts/backup-device.sh` を検証するための esptool 代替です。

本物の esptool と同じ引数だけを受け取り、読み出し系のサブコマンドにだけ応答します。
消去や書き込みのサブコマンドは実行せず、失敗として記録します。

環境変数:
  FAKE_ESPTOOL_SCENARIO  応答内容を書いた JSON ファイルのパス
  FAKE_ESPTOOL_LOG       呼び出された引数を 1 行ずつ追記するファイルのパス
"""

import json
import os
import sys
from pathlib import Path


READ_ONLY_COMMANDS = ("version", "flash_id", "read_flash")
VALUE_OPTIONS = ("--port", "--baud", "--chip", "--before", "--after")


def load_scenario() -> dict:
    return json.loads(Path(os.environ["FAKE_ESPTOOL_SCENARIO"]).read_text(encoding="utf-8"))


def record(argv: list[str]) -> None:
    with open(os.environ["FAKE_ESPTOOL_LOG"], "a", encoding="utf-8") as handle:
        handle.write(" ".join(argv) + "\n")


def previous_calls() -> list[list[str]]:
    log = Path(os.environ["FAKE_ESPTOOL_LOG"])
    if not log.exists():
        return []
    return [line.split(" ") for line in log.read_text(encoding="utf-8").splitlines()]


def split_command(argv: list[str]) -> tuple[str, list[str]]:
    index = 0
    while index < len(argv):
        token = argv[index]
        if token in VALUE_OPTIONS:
            index += 2
        elif token.startswith("-"):
            index += 1
        else:
            return token, argv[index + 1 :]
    return "", []


def do_read_flash(scenario: dict, call_index: int, args: list[str]) -> int:
    offset, size, destination = int(args[0], 0), int(args[1], 0), args[2]
    image = Path(scenario["image"]).read_bytes()
    chunk = bytearray(image[offset : offset + size])

    if offset == 0:
        short = scenario.get("short_read_bytes")
        if short is not None:
            chunk = chunk[:short]
        if scenario.get("corrupt_second_read") and call_index == 1:
            chunk[0x100] ^= 0xFF

    Path(destination).write_bytes(bytes(chunk))
    print(f"Read {len(chunk)} bytes at 0x{offset:x} in 1.0 seconds...")
    print("Hard resetting via RTS pin...")
    return 0


def main(argv: list[str]) -> int:
    record(argv)
    calls = previous_calls()
    command, args = split_command(argv)

    if command not in READ_ONLY_COMMANDS:
        print(f"fake esptool: 読み出し以外のサブコマンドは実行しません: {command}", file=sys.stderr)
        return 2

    scenario = load_scenario()
    if command == "version":
        print("esptool.py v4.7.0")
        return 0
    if command == "flash_id":
        print(scenario["flash_id_output"].rstrip("\n"))
        return 0

    full_reads = 0
    for call in calls[:-1]:
        earlier, earlier_args = split_command(call)
        if earlier == "read_flash" and int(earlier_args[0], 0) == 0:
            full_reads += 1
    return do_read_flash(scenario, full_reads, args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
