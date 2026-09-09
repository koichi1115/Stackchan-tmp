#!/usr/bin/env bash
# 検証済みバックアップを実機のオフセット 0 へ書き戻します。消去は行いません。
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PORT=""
BACKUP_DIR=""
BAUD="460800"
ASSUME_YES="no"

usage() {
	cat <<'USAGE'
使い方: scripts/restore-device.sh --port <シリアルポート> --backup-dir <バックアップ先> [--baud 460800] [--yes]

例: scripts/restore-device.sh --port /dev/ttyUSB0 --backup-dir backups/20260909T101500Z

このスクリプトはフラッシュを消去しません。バックアップの SHA-256 を検証してから書き戻し、
書き戻し後に読み返して SHA-256 が一致することを確認します。
USAGE
}

while (($# > 0)); do
	case "$1" in
	--port)
		PORT="${2:-}"
		shift 2
		;;
	--backup-dir)
		BACKUP_DIR="${2:-}"
		shift 2
		;;
	--baud)
		BAUD="${2:-}"
		shift 2
		;;
	--yes)
		ASSUME_YES="yes"
		shift
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

if [[ -z "$PORT" || -z "$BACKUP_DIR" ]]; then
	echo "--port と --backup-dir の両方を指定してください。" >&2
	usage >&2
	exit 2
fi

IMAGE="$BACKUP_DIR/full-flash.bin"
MANIFEST="$BACKUP_DIR/manifest.txt"
for required in "$IMAGE" "$MANIFEST" "$BACKUP_DIR/SHA256SUMS"; do
	if [[ ! -f "$required" ]]; then
		echo "必要なファイルがありません: $required" >&2
		exit 2
	fi
done

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

EXPECTED_HASH="$(sed -n 's/^full_flash_sha256=//p' "$MANIFEST" | head -n 1)"
EXPECTED_BYTES="$(sed -n 's/^full_flash_bytes=//p' "$MANIFEST" | head -n 1)"
FLASH_LABEL="$(sed -n 's/^flash_size_label=//p' "$MANIFEST" | head -n 1)"
MANIFEST_CHIP="$(sed -n 's/^chip=//p' "$MANIFEST" | head -n 1)"

echo "[1/5] バックアップの SHA-256 を検証します。"
(cd "$BACKUP_DIR" && sha256sum --check --status SHA256SUMS) || {
	echo "バックアップのチェックサムが一致しません。書き戻しを中止します。" >&2
	exit 1
}

ACTUAL_HASH="$(sha256sum "$IMAGE" | cut -d ' ' -f 1)"
ACTUAL_BYTES="$(stat -c %s "$IMAGE")"
if [[ "$ACTUAL_HASH" != "$EXPECTED_HASH" || "$ACTUAL_BYTES" != "$EXPECTED_BYTES" ]]; then
	echo "バックアップが manifest.txt と一致しません。書き戻しを中止します。" >&2
	exit 1
fi

echo "[2/5] 接続中の実機を確認します。"
"${ESPTOOL[@]}" --port "$PORT" --baud "$BAUD" flash_id 2>&1 | tee "$BACKUP_DIR/restore-flash-id.log"
CURRENT_LABEL="$(sed -n 's/^Detected flash size: \(.*\)$/\1/p' "$BACKUP_DIR/restore-flash-id.log" | head -n 1)"
CURRENT_CHIP="$(sed -n 's/^Chip is \(.*\)$/\1/p' "$BACKUP_DIR/restore-flash-id.log" | head -n 1)"

if [[ "$CURRENT_LABEL" != "$FLASH_LABEL" ]]; then
	echo "フラッシュ容量がバックアップ時と異なります（$CURRENT_LABEL != $FLASH_LABEL）。中止します。" >&2
	exit 1
fi
if [[ -n "$MANIFEST_CHIP" && "$MANIFEST_CHIP" != "unknown" && "$CURRENT_CHIP" != "$MANIFEST_CHIP" ]]; then
	echo "チップがバックアップ時と異なります（$CURRENT_CHIP != $MANIFEST_CHIP）。中止します。" >&2
	exit 1
fi

if [[ "$ASSUME_YES" != "yes" ]]; then
	echo
	echo "$IMAGE をオフセット 0 へ書き戻します（$ACTUAL_BYTES バイト）。"
	read -r -p "続行するには RESTORE と入力してください: " CONFIRM
	if [[ "$CONFIRM" != "RESTORE" ]]; then
		echo "中止しました。"
		exit 1
	fi
fi

echo "[3/5] オフセット 0 へ書き戻します。"
"${ESPTOOL[@]}" --port "$PORT" --baud "$BAUD" write_flash --flash_size keep 0x0 "$IMAGE" 2>&1 |
	tee "$BACKUP_DIR/restore-write.log"

echo "[4/5] 書き戻した内容を読み返します。"
READBACK="$(mktemp)"
trap 'rm -f "$READBACK"' EXIT
"${ESPTOOL[@]}" --port "$PORT" --baud "$BAUD" read_flash 0 "$ACTUAL_BYTES" "$READBACK" 2>&1 |
	tee "$BACKUP_DIR/restore-readback.log"

echo "[5/5] SHA-256 を照合します。"
READBACK_HASH="$(sha256sum "$READBACK" | cut -d ' ' -f 1)"
if [[ "$READBACK_HASH" != "$EXPECTED_HASH" ]]; then
	echo "書き戻し後の SHA-256 が一致しません（$READBACK_HASH != $EXPECTED_HASH）。" >&2
	echo "電源を切らずに、もう一度このスクリプトを実行してください。" >&2
	exit 1
fi

echo
echo "復元成功。SHA-256 は $EXPECTED_HASH です。純正ファームウェアに戻りました。"
