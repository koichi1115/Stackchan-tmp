"""標準ライブラリだけで書いた最小の WebSocket（RFC 6455）枠組み。

ロボットとの常時接続に使います。外部依存を増やさないために自前で持ちます。
サーバー側（リレー）とクライアント側（device_sim）の両方で使います。
"""

from dataclasses import dataclass, field
import base64
import hashlib
import os
import struct
import threading
from typing import BinaryIO

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

OP_CONTINUATION = 0x0
OP_TEXT = 0x1
OP_BINARY = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA

MAX_MESSAGE_BYTES = 4 * 1024 * 1024


class WebSocketError(RuntimeError):
    pass


class ConnectionClosed(WebSocketError):
    pass


def accept_key(client_key: str) -> str:
    digest = hashlib.sha1((client_key.strip() + GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def client_key() -> str:
    return base64.b64encode(os.urandom(16)).decode("ascii")


def encode_frame(opcode: int, payload: bytes, mask: bool) -> bytes:
    header = bytearray([0x80 | opcode])
    length = len(payload)
    mask_bit = 0x80 if mask else 0
    if length < 126:
        header.append(mask_bit | length)
    elif length < 65_536:
        header.append(mask_bit | 126)
        header += struct.pack("!H", length)
    else:
        header.append(mask_bit | 127)
        header += struct.pack("!Q", length)
    if not mask:
        return bytes(header) + payload
    key = os.urandom(4)
    header += key
    masked = bytes(byte ^ key[index % 4] for index, byte in enumerate(payload))
    return bytes(header) + masked


@dataclass
class WebSocket:
    """一本の接続。読み取りは一つのスレッドから、送信は複数スレッドから呼べます。"""

    reader: BinaryIO
    writer: BinaryIO
    is_client: bool
    max_message_bytes: int = MAX_MESSAGE_BYTES
    _send_lock: threading.Lock = field(default_factory=threading.Lock)
    _closed: bool = False

    def send_text(self, text: str) -> None:
        self._send(OP_TEXT, text.encode("utf-8"))

    def send_binary(self, payload: bytes) -> None:
        self._send(OP_BINARY, payload)

    def close(self, code: int = 1000) -> None:
        if self._closed:
            return
        try:
            self._send(OP_CLOSE, struct.pack("!H", code))
        except OSError:
            pass
        self._closed = True

    def receive(self) -> tuple[int, bytes]:
        """一メッセージを返します。ping には自動で pong を返し、close は例外にします。"""
        message = bytearray()
        message_opcode: int | None = None
        while True:
            fin, opcode, payload = self._read_frame()
            if opcode == OP_PING:
                self._send(OP_PONG, payload)
                continue
            if opcode == OP_PONG:
                continue
            if opcode == OP_CLOSE:
                self._closed = True
                raise ConnectionClosed("相手が接続を閉じました。")
            if opcode == OP_CONTINUATION:
                if message_opcode is None:
                    raise WebSocketError("継続フレームの前にメッセージがありません。")
            else:
                if message_opcode is not None:
                    raise WebSocketError("メッセージの途中で新しいメッセージが始まりました。")
                message_opcode = opcode
            message += payload
            if len(message) > self.max_message_bytes:
                raise WebSocketError("メッセージが大きすぎます。")
            if fin:
                return message_opcode, bytes(message)

    def _send(self, opcode: int, payload: bytes) -> None:
        frame = encode_frame(opcode, payload, mask=self.is_client)
        with self._send_lock:
            self.writer.write(frame)
            self.writer.flush()

    def _read_exact(self, count: int) -> bytes:
        data = self.reader.read(count)
        if data is None or len(data) < count:
            self._closed = True
            raise ConnectionClosed("接続が切れました。")
        return data

    def _read_frame(self) -> tuple[bool, int, bytes]:
        first, second = self._read_exact(2)
        fin = bool(first & 0x80)
        if first & 0x70:
            raise WebSocketError("拡張ビットは扱いません。")
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        length = second & 0x7F
        if length == 126:
            (length,) = struct.unpack("!H", self._read_exact(2))
        elif length == 127:
            (length,) = struct.unpack("!Q", self._read_exact(8))
        if length > self.max_message_bytes:
            raise WebSocketError("フレームが大きすぎます。")
        # クライアントからのフレームは必ずマスクされ、サーバーからのフレームはされません。
        if masked == self.is_client:
            raise WebSocketError("マスクの有無が仕様と違います。")
        key = self._read_exact(4) if masked else b""
        payload = self._read_exact(length) if length else b""
        if masked:
            payload = bytes(byte ^ key[index % 4] for index, byte in enumerate(payload))
        return fin, opcode, payload
