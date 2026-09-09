#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
export PYTHONPATH="$ROOT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"

MOCK_PORT="${MOCK_WEBHOOK_PORT:-18765}"
RELAY_TEST_PORT="${RELAY_INTEGRATION_PORT:-18766}"
TMP_DIR="$(mktemp -d)"

cleanup() {
	kill "${RELAY_PID:-}" "${MOCK_PID:-}" 2>/dev/null || true
	wait "${RELAY_PID:-}" "${MOCK_PID:-}" 2>/dev/null || true
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

OUTPUT="$(
	RELAY_URL="http://127.0.0.1:$RELAY_TEST_PORT/utterance" \
	python3 -m stackchan_grok_relay.mock_stackchan "こんにちは"
)"
printf '%s\n' "$OUTPUT"

if [[ "$OUTPUT" != "SPOKEN: こんにちは。" ]]; then
	echo "オフライン結合検証の出力が一致しません。" >&2
	exit 1
fi
