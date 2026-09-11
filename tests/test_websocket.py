import io
import unittest

from stackchan_grok_relay.websocket import (
    OP_BINARY,
    OP_CLOSE,
    OP_PING,
    OP_TEXT,
    ConnectionClosed,
    WebSocket,
    WebSocketError,
    accept_key,
    encode_frame,
)


def server_reading(frames: bytes) -> tuple[WebSocket, io.BytesIO]:
    out = io.BytesIO()
    return WebSocket(reader=io.BytesIO(frames), writer=out, is_client=False), out


class WebSocketFramingTests(unittest.TestCase):
    def test_accept_key_matches_rfc_example(self) -> None:
        self.assertEqual(accept_key("dGhlIHNhbXBsZSBub25jZQ=="), "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")

    def test_masked_text_frame_roundtrip(self) -> None:
        frame = encode_frame(OP_TEXT, "こんにちは".encode("utf-8"), mask=True)
        link, _ = server_reading(frame)
        self.assertEqual(link.receive(), (OP_TEXT, "こんにちは".encode("utf-8")))

    def test_medium_and_large_binary_frames(self) -> None:
        medium = bytes(range(256)) * 10  # 2560 bytes → 16 bit 長
        large = bytes(70_000)  # 64 bit 長
        link, _ = server_reading(encode_frame(OP_BINARY, medium, True) + encode_frame(OP_BINARY, large, True))
        self.assertEqual(link.receive(), (OP_BINARY, medium))
        self.assertEqual(link.receive(), (OP_BINARY, large))

    def test_unmasked_client_frame_is_rejected(self) -> None:
        link, _ = server_reading(encode_frame(OP_TEXT, b"x", mask=False))
        with self.assertRaises(WebSocketError):
            link.receive()

    def test_ping_gets_pong_and_is_skipped(self) -> None:
        frames = encode_frame(OP_PING, b"hi", True) + encode_frame(OP_TEXT, b"ok", True)
        link, out = server_reading(frames)
        self.assertEqual(link.receive(), (OP_TEXT, b"ok"))
        self.assertEqual(out.getvalue(), encode_frame(0xA, b"hi", mask=False))

    def test_close_raises(self) -> None:
        link, _ = server_reading(encode_frame(OP_CLOSE, b"\x03\xe8", True))
        with self.assertRaises(ConnectionClosed):
            link.receive()

    def test_eof_raises_connection_closed(self) -> None:
        link, _ = server_reading(b"\x81")
        with self.assertRaises(ConnectionClosed):
            link.receive()

    def test_fragmented_message_is_reassembled(self) -> None:
        first = bytearray(encode_frame(OP_TEXT, b"ab", True))
        first[0] &= 0x7F  # FIN を落とす
        second = encode_frame(0x0, b"cd", True)
        link, _ = server_reading(bytes(first) + second)
        self.assertEqual(link.receive(), (OP_TEXT, b"abcd"))

    def test_server_sends_unmasked(self) -> None:
        link, out = server_reading(b"")
        link.send_text("x")
        self.assertEqual(out.getvalue(), b"\x81\x01x")

    def test_message_size_limit(self) -> None:
        link, _ = server_reading(encode_frame(OP_BINARY, bytes(2_000), True))
        link.max_message_bytes = 1_000
        with self.assertRaises(WebSocketError):
            link.receive()
