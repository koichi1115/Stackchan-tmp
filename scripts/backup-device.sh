#!/usr/bin/env bash
# 実機のフラッシュ全体をバックアップします。書き込みも消去も行いません。
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PORT=""
BAUD="460800"
OUTPUT_ROOT="backups"
BANNER_SECONDS="6"

usage() {
	cat <<'USAGE'
使い方: scripts/backup-device.sh --port <シリアルポート> [--baud 460800] [--output-dir backups] [--banner-seconds 6]

例: scripts/backup-device.sh --port /dev/ttyUSB0

このスクリプトはフラッシュを読み出すだけです。消去も書き込みも行いません。
USAGE
}

while (($# > 0)); do
	case "$1" in
	--port)
		PORT="${2:-}"
		shift 2
		;;
	--baud)
		BAUD="${2:-}"
		shift 2
		;;
	--output-dir)
		OUTPUT_ROOT="${2:-}"
		shift 2
		;;
	--banner-seconds)
		BANNER_SECONDS="${2:-}"
		shift 2
		;;
	-h | --help)
		usage
		exit 0
		;;
	*)
		echo "不明な引数です: $1" >&2
		usage >&2
		exit 2
		;;
	esac
done

if [[ -z "$PORT" ]]; then
	echo "--port を指定してください。" >&2
	usage >&2
	exit 2
fi

if [[ ! "$PORT" =~ ^COM[0-9]+$ && ! -e "$PORT" ]]; then
	echo "シリアルポートが見つかりません: $PORT" >&2
	exit 2
fi

if command -v esptool.py >/dev/null 2>&1; then
	ESPTOOL=(esptool.py)
elif command -v esptool >/dev/null 2>&1; then
	ESPTOOL=(esptool)
elif python3 -c "import esptool" >/dev/null 2>&1; then
	ESPTOOL=(python3 -m esptool)
else
	echo "esptool が見つかりません。'pip install esptool' を実行してください。" >&2
	exit 2
fi

if ! command -v sha256sum >/dev/null 2>&1; then
	echo "sha256sum が見つかりません。" >&2
	exit 2
fi

# ファイルの大きさ（バイト）。GNU の stat -c と BSD の stat -f の差を避けるため wc を使います。
file_size() {
	wc -c <"$1" | tr -d '[:space:]'
}

TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="$OUTPUT_ROOT/$TIMESTAMP"
mkdir -p "$BACKUP_DIR"

FULL_IMAGE="$BACKUP_DIR/full-flash.bin"
VERIFY_IMAGE="$BACKUP_DIR/full-flash.verify.bin"
PARTITION_IMAGE="$BACKUP_DIR/partition-table.bin"

echo "バックアップ先: $BACKUP_DIR"
echo "[1/7] チップとフラッシュ容量を検出します。"
"${ESPTOOL[@]}" version >"$BACKUP_DIR/esptool-version.txt" 2>&1 || true
"${ESPTOOL[@]}" --port "$PORT" --baud "$BAUD" flash_id 2>&1 | tee "$BACKUP_DIR/chip-info.txt"

CHIP="$(sed -n -E 's/^Chip (is|type:) *(.*)$/\2/p' "$BACKUP_DIR/chip-info.txt" | head -n 1)"
FLASH_SIZE_LABEL="$(sed -n 's/^Detected flash size: \(.*\)$/\1/p' "$BACKUP_DIR/chip-info.txt" | head -n 1)"

if [[ -z "$FLASH_SIZE_LABEL" ]]; then
	echo "フラッシュ容量を検出できませんでした。中止します。" >&2
	echo "$BACKUP_DIR/chip-info.txt の内容を確認してください。容量を仮定して読み出すことはしません。" >&2
	exit 1
fi

FLASH_BYTES="$(python3 scripts/flash_tools.py flash-size "$FLASH_SIZE_LABEL")" || {
	echo "フラッシュ容量を解釈できませんでした。中止します。" >&2
	exit 1
}

echo "検出結果: チップ=${CHIP:-不明} フラッシュ=$FLASH_SIZE_LABEL ($FLASH_BYTES バイト)"

echo "[2/7] 起動バナーを記録します（取得できない場合も続行します）。"
if python3 -c "import serial" >/dev/null 2>&1; then
	python3 - "$PORT" "$BANNER_SECONDS" >"$BACKUP_DIR/boot-banner.txt" 2>&1 <<'PY' || true
import sys
import time

import serial

port, seconds = sys.argv[1], float(sys.argv[2])
with serial.Serial(port, 115200, timeout=0.2) as link:
    link.setDTR(False)
    link.setRTS(True)
    time.sleep(0.1)
    link.setRTS(False)
    deadline = time.time() + seconds
    while time.time() < deadline:
        chunk = link.read(4096)
        if chunk:
            sys.stdout.write(chunk.decode("utf-8", "replace"))
            sys.stdout.flush()
PY
else
	echo "pyserial が無いため起動バナーを取得できませんでした。" >"$BACKUP_DIR/boot-banner.txt"
fi

echo "[3/7] フラッシュ全体を読み出します（1 回目）。"
"${ESPTOOL[@]}" --port "$PORT" --baud "$BAUD" read_flash 0 "$FLASH_BYTES" "$FULL_IMAGE" 2>&1 | tee "$BACKUP_DIR/read-1.log"

ACTUAL_BYTES="$(file_size "$FULL_IMAGE")"
if [[ "$ACTUAL_BYTES" != "$FLASH_BYTES" ]]; then
	echo "読み出し長が検出容量と一致しません（$ACTUAL_BYTES != ${FLASH_BYTES}）。バックアップは無効です。" >&2
	exit 1
fi

echo "[4/7] フラッシュ全体をもう一度読み出します（2 回目）。"
"${ESPTOOL[@]}" --port "$PORT" --baud "$BAUD" read_flash 0 "$FLASH_BYTES" "$VERIFY_IMAGE" 2>&1 | tee "$BACKUP_DIR/read-2.log"

VERIFY_BYTES="$(file_size "$VERIFY_IMAGE")"
if [[ "$VERIFY_BYTES" != "$FLASH_BYTES" ]]; then
	echo "2 回目の読み出し長が検出容量と一致しません（$VERIFY_BYTES != ${FLASH_BYTES}）。バックアップは無効です。" >&2
	exit 1
fi

HASH_1="$(sha256sum "$FULL_IMAGE" | cut -d ' ' -f 1)"
HASH_2="$(sha256sum "$VERIFY_IMAGE" | cut -d ' ' -f 1)"
if [[ "$HASH_1" != "$HASH_2" ]]; then
	echo "2 回の読み出しの SHA-256 が一致しません。バックアップは無効です。書き込みへ進まないでください。" >&2
	echo "1 回目: $HASH_1" >&2
	echo "2 回目: $HASH_2" >&2
	exit 1
fi
rm -f "$VERIFY_IMAGE"

echo "[5/7] パーティションテーブル領域を個別に読み出します。"
PARTITION_OFFSET="$(python3 scripts/flash_tools.py table-offset "$FULL_IMAGE")" || {
	echo "パーティションテーブルの位置を特定できませんでした。中止します。" >&2
	exit 1
}

"${ESPTOOL[@]}" --port "$PORT" --baud "$BAUD" read_flash "$PARTITION_OFFSET" 0xC00 "$PARTITION_IMAGE" 2>&1 |
	tee "$BACKUP_DIR/read-partition.log"

python3 scripts/flash_tools.py describe-table "$FULL_IMAGE" "$PARTITION_IMAGE" "$PARTITION_OFFSET" \
	>"$BACKUP_DIR/partition-table.txt" || {
	echo "パーティションテーブルの個別読み出しを検証できませんでした。中止します。" >&2
	exit 1
}

echo "[6/7] 純正ファームウェアのバージョン文字列を記録します。"
python3 scripts/flash_tools.py app-info "$FULL_IMAGE" "$PARTITION_IMAGE" \
	>"$BACKUP_DIR/firmware-version.txt"

echo "[7/7] チェックサムと目録を書き出します。"
(cd "$BACKUP_DIR" && sha256sum full-flash.bin partition-table.bin >SHA256SUMS)

cat >"$BACKUP_DIR/manifest.txt" <<MANIFEST
created_utc=$TIMESTAMP
port=$PORT
baud=$BAUD
chip=${CHIP:-unknown}
flash_size_label=$FLASH_SIZE_LABEL
flash_size_bytes=$FLASH_BYTES
partition_table_offset=$PARTITION_OFFSET
full_flash_sha256=$HASH_1
full_flash_bytes=$ACTUAL_BYTES
MANIFEST

echo
echo "バックアップ先: $(cd "$BACKUP_DIR" && pwd)"
echo "ファイル: full-flash.bin"
echo "サイズ: $ACTUAL_BYTES バイト（検出容量 $FLASH_SIZE_LABEL と一致）"
echo "SHA-256: $HASH_1"
cat "$BACKUP_DIR/firmware-version.txt"
echo
echo "バックアップ成功。2 回の読み出しが SHA-256 で一致しました。書き込みへ進んで構いません。"
