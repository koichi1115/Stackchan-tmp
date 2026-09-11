#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
export PYTHONPATH="$ROOT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"
# 出力の比較を文字コードに左右されないようにする（Windows の Git Bash でも同じ結果にする）
export PYTHONIOENCODING=utf-8
export PYTHONUTF8=1

MOCK_PORT="${MOCK_WEBHOOK_PORT:-18765}"
RELAY_TEST_PORT="${RELAY_INTEGRATION_PORT:-18766}"
TMP_DIR="$(mktemp -d)"

cleanup() {
	kill "${RELAY_PID:-}" "${MOCK_PID:-}" "${STREAM_PID:-}" 2>/dev/null || true
	wait "${RELAY_PID:-}" "${MOCK_PID:-}" "${STREAM_PID:-}" 2>/dev/null || true
	rm -rf "$TMP_DIR"
}
trap cleanup EXIT

wait_for_port() {
	python3 - "$1" "$2" <<'PY'
import socket
import sys
import time

host = sys.argv[1]
port = int(sys.argv[2])
for _ in range(50):
    try:
        with socket.create_connection((host, port), timeout=0.1):
            raise SystemExit(0)
    except OSError:
        time.sleep(0.1)
raise SystemExit("ローカルサーバーの起動を確認できませんでした。")
PY
}

python3 tests/mock_grok_webhook.py --port "$MOCK_PORT" >"$TMP_DIR/mock.log" 2>&1 &
MOCK_PID=$!
wait_for_port 127.0.0.1 "$MOCK_PORT"

RELAY_HOST=127.0.0.1 \
RELAY_PORT="$RELAY_TEST_PORT" \
GROK_WEBHOOK_URL="http://127.0.0.1:$MOCK_PORT/grok" \
GROK_WEBHOOK_SENDER_KEY="test-only-placeholder" \
python3 -m stackchan_grok_relay >"$TMP_DIR/relay.log" 2>&1 &
RELAY_PID=$!
wait_for_port 127.0.0.1 "$RELAY_TEST_PORT"

echo "--- リレーの待受ログ ---"
cat "$TMP_DIR/relay.log"
echo "--- テキスト経路（s1 の /utterance）---"

OUTPUT="$(
	RELAY_URL="http://127.0.0.1:$RELAY_TEST_PORT/utterance" \
	python3 -m stackchan_grok_relay.mock_stackchan "こんにちは"
)"
printf '%s\n' "$OUTPUT"

if [[ "$OUTPUT" != "SPOKEN: こんにちは。" ]]; then
	echo "オフライン結合検証の出力が一致しません。" >&2
	exit 1
fi

echo "--- 実機プロトコル（/device/utterance、モック STT・モック TTS）---"

DEVICE_OUTPUT="$(
	python3 -m stackchan_grok_relay.device_sim \
		--relay-url "http://127.0.0.1:$RELAY_TEST_PORT/device/utterance" \
		--wav tests/fixtures/utterance.wav \
		--save-reply "$TMP_DIR/reply.wav"
)"
printf '%s\n' "$DEVICE_OUTPUT"

if [[ "$DEVICE_OUTPUT" != "SPOKEN: こんにちは。" ]]; then
	echo "実機プロトコル検証の出力が一致しません。" >&2
	exit 1
fi

if [[ ! -s "$TMP_DIR/reply.wav" ]]; then
	echo "発話音声を受け取れませんでした。" >&2
	exit 1
fi

echo "--- WebSocket 経路（/device/stream、ウェイクワード付きモック STT）---"

# モック STT は固定の文字起こしを返すので、ウェイクワード付きの文にしてリレーを別ポートで起動する
RELAY_STREAM_PORT="${RELAY_STREAM_PORT:-18767}"
RELAY_HOST=127.0.0.1 RELAY_PORT="$RELAY_STREAM_PORT" GROK_WEBHOOK_URL="http://127.0.0.1:$MOCK_PORT/grok" GROK_WEBHOOK_SENDER_KEY="test-only-placeholder" MOCK_TRANSCRIPT="スタックちゃん、こんにちは" python3 -m stackchan_grok_relay >"$TMP_DIR/relay-stream.log" 2>&1 &
STREAM_PID=$!
wait_for_port 127.0.0.1 "$RELAY_STREAM_PORT"

STREAM_OUTPUT="$(
	python3 -m stackchan_grok_relay.stream_sim 		--relay-url "ws://127.0.0.1:$RELAY_STREAM_PORT/device/stream" 		--wav tests/fixtures/utterance.wav 		--save-reply "$TMP_DIR/stream-reply.wav" 2>/dev/null
)"
printf '%s
' "$STREAM_OUTPUT"
kill "$STREAM_PID" 2>/dev/null || true
wait "$STREAM_PID" 2>/dev/null || true

if [[ "$STREAM_OUTPUT" != "SPOKEN: こんにちは。" ]]; then
	echo "WebSocket 経路の出力が一致しません。" >&2
	cat "$TMP_DIR/relay-stream.log" >&2
	exit 1
fi

if [[ ! -s "$TMP_DIR/stream-reply.wav" ]]; then
	echo "WebSocket 経路で発話音声を受け取れませんでした。" >&2
	exit 1
fi

echo "--- 無音（Webhook 到達不可）---"
kill "$MOCK_PID" 2>/dev/null || true
wait "$MOCK_PID" 2>/dev/null || true
MOCK_PID=""

SILENT_OUTPUT="$(
	python3 -m stackchan_grok_relay.device_sim \
		--relay-url "http://127.0.0.1:$RELAY_TEST_PORT/device/utterance" \
		--wav tests/fixtures/utterance.wav 2>/dev/null
)"

if [[ -n "$SILENT_OUTPUT" ]]; then
	echo "Webhook 失敗時に発話してはいけません: $SILENT_OUTPUT" >&2
	exit 1
fi
echo "Webhook 失敗時は無音でした。"
