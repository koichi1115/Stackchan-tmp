"""WebSocket 経路（s4）の検証用クライアント。実機と同じ手順で音声を流します。

WAV を 1024 サンプルずつ送り、そのあと無音を流して発話の終わりを作り、
`speak` が届いたら WAV を受け取り、標準出力へ `SPOKEN: <発話>` を出して `played` を返します。
"""

import argparse
import json
from pathlib import Path
import queue
import socket
import sys
import threading
import time
from urllib.parse import urlparse

from .vad import FRAME_SAMPLES
from .wav import DEVICE_SAMPLE_RATE, WavError, decode_wav
from .websocket import OP_BINARY, OP_TEXT, ConnectionClosed, WebSocket, WebSocketError, accept_key, client_key

DEFAULT_FIXTURE = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "utterance.wav"
FRAME_BYTES = FRAME_SAMPLES * 2


def connect(url: str, timeout: float) -> tuple[socket.socket, WebSocket]:
    parsed = urlparse(url)
    if parsed.scheme != "ws" or not parsed.hostname:
        raise ValueError("ws:// の URL を指定してください。")
    port = parsed.port or 80
    key = client_key()
    raw = socket.create_connection((parsed.hostname, port), timeout=timeout)
    request = (
        f"GET {parsed.path or '/'} HTTP/1.1\r\n"
        f"Host: {parsed.hostname}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n\r\n"
    )
    raw.sendall(request.encode("ascii"))
    reader = raw.makefile("rb")
    writer = raw.makefile("wb")
    status_line = reader.readline().decode("latin-1")
    headers: dict[str, str] = {}
    while True:
        line = reader.readline().decode("latin-1")
        if line in ("\r\n", "\n", ""):
            break
        name, _, value = line.partition(":")
        headers[name.strip().lower()] = value.strip()
    if " 101 " not in status_line:
        raise ConnectionError(f"WebSocket の握手に失敗しました。{status_line.strip()}")
    if headers.get("sec-websocket-accept") != accept_key(key):
        raise ConnectionError("WebSocket の握手の応答が不正です。")
    # 読み取りは別スレッドで行うので、ソケットのタイムアウトは外します（タイムアウト後の
    # バッファ付き読み取りは壊れるため）。
    raw.settimeout(None)
    return raw, WebSocket(reader=reader, writer=writer, is_client=True)


def start_receiver(link: WebSocket) -> "queue.Queue[tuple[int, bytes] | None]":
    """受信専用スレッドを起こし、メッセージをキューへ流します。切断時は None を入れます。"""
    inbox: "queue.Queue[tuple[int, bytes] | None]" = queue.Queue()

    def pump() -> None:
        try:
            while True:
                inbox.put(link.receive())
        except (ConnectionClosed, WebSocketError, OSError):
            inbox.put(None)

    threading.Thread(target=pump, daemon=True).start()
    return inbox


def pcm_frames(audio: bytes) -> list[bytes]:
    decoded = decode_wav(audio)
    if decoded.sample_rate != DEVICE_SAMPLE_RATE or decoded.channels != 1 or decoded.sample_width != 2:
        raise WavError("16 kHz モノラル 16 bit の WAV を指定してください。")
    frames = decoded.frames
    return [frames[offset : offset + FRAME_BYTES] for offset in range(0, len(frames), FRAME_BYTES)]


def stream_once(
    url: str,
    audio: bytes,
    timeout: float,
    trailing_silence_seconds: float = 1.5,
    save_reply: Path | None = None,
    send_button: bool = False,
) -> str | None:
    """一発話を流し、返ってきた発話テキストを返します。無音なら None です。"""
    raw, link = connect(url, timeout)
    deadline = time.monotonic() + timeout
    messages = start_receiver(link)
    try:
        link.send_text(
            json.dumps({"type": "hello", "device": "sim", "sample_rate": DEVICE_SAMPLE_RATE, "firmware": "sim"})
        )
        if send_button:
            link.send_text(json.dumps({"type": "button", "name": "A"}))
        for frame in pcm_frames(audio):
            link.send_binary(frame.ljust(FRAME_BYTES, b"\0"))
        silent_frames = int(trailing_silence_seconds * DEVICE_SAMPLE_RATE / FRAME_SAMPLES) + 1
        for _ in range(silent_frames):
            link.send_binary(bytes(FRAME_BYTES))

        expected = 0
        spoken = ""
        received = bytearray()
        while time.monotonic() < deadline:
            try:
                item = messages.get(timeout=max(0.05, deadline - time.monotonic()))
            except queue.Empty:
                break
            if item is None:
                break
            opcode, payload = item
            if opcode == OP_TEXT:
                message = json.loads(payload.decode("utf-8"))
                if message.get("type") == "speak":
                    expected = int(message.get("bytes", 0))
                    spoken = str(message.get("text", ""))
                    received = bytearray()
                elif message.get("type") == "state":
                    print(f"状態: {message.get('state')}", file=sys.stderr)
            elif opcode == OP_BINARY and expected:
                received += payload
                if len(received) >= expected:
                    reply = decode_wav(bytes(received))
                    print(
                        f"再生: {reply.frame_count} フレーム {reply.sample_rate} Hz {reply.channels} ch",
                        file=sys.stderr,
                    )
                    if save_reply is not None:
                        save_reply.write_bytes(bytes(received))
                    link.send_text(json.dumps({"type": "played"}))
                    return spoken
        return None
    finally:
        try:
            link.close()
        except (OSError, WebSocketError, ConnectionClosed):
            pass
        raw.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="WebSocket 経路の検証用シミュレーターです。")
    parser.add_argument("--relay-url", default="ws://127.0.0.1:8787/device/stream")
    parser.add_argument("--wav", type=Path, default=DEFAULT_FIXTURE, help="流す録音 WAV（16 kHz モノラル）")
    parser.add_argument("--save-reply", type=Path, default=None)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--button", action="store_true", help="先にボタン A を押した扱いにする")
    args = parser.parse_args()

    try:
        audio = args.wav.read_bytes()
    except OSError as error:
        print(f"録音を読み込めませんでした。{error}", file=sys.stderr)
        return 2
    try:
        spoken = stream_once(
            args.relay_url, audio, args.timeout, save_reply=args.save_reply, send_button=args.button
        )
    except (OSError, ValueError, WebSocketError) as error:
        print(f"リレーとやり取りできませんでした。{error}", file=sys.stderr)
        return 1
    if spoken is None:
        print("無音です。何も再生しません。", file=sys.stderr)
        return 0
    print(f"SPOKEN: {spoken}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
