import contextlib
import io
from http.server import ThreadingHTTPServer
import json
import threading
import unittest

from stackchan_grok_relay.grok import GrokError, GrokResult
from stackchan_grok_relay.relay import RelayService, StreamService, _handler_for
from stackchan_grok_relay.session import SessionRegistry
from stackchan_grok_relay.speech import MockTextToSpeech, SpeechError
from stackchan_grok_relay.stream_sim import connect, pcm_frames, stream_once
from stackchan_grok_relay.vad import FRAME_SAMPLES
from stackchan_grok_relay.wake import WakeWordMatcher
from stackchan_grok_relay.wav import decode_wav, tone_wav
from stackchan_grok_relay.websocket import OP_BINARY, OP_TEXT


class FakeClient:
    def __init__(self) -> None:
        self.received: list[str] = []

    def reply(self, utterance) -> GrokResult:
        self.received.append(utterance.text)
        if utterance.text == "こんにちは":
            return GrokResult(reply="こんにちは。二文目です。")
        return GrokResult(
            reply="",
            error=GrokError(code="timeout", message="時間切れです。", retryable=True, attempts=2),
        )


class ScriptedSpeechToText:
    """呼ばれるたびに次の文字起こしを返します。"""

    def __init__(self, transcripts: list[str]) -> None:
        self.transcripts = list(transcripts)
        self.calls = 0

    def transcribe(self, audio: bytes) -> str:
        self.calls += 1
        decode_wav(audio)
        if not self.transcripts:
            return ""
        return self.transcripts.pop(0)


@contextlib.contextmanager
def running_relay(transcripts: list[str], wake_window_seconds: float = 8.0):
    client = FakeClient()
    relay = RelayService(client=client, max_reply_length=120)
    stt = ScriptedSpeechToText(transcripts)
    registry = SessionRegistry()
    streams = StreamService(
        relay=relay,
        speech_to_text=stt,
        text_to_speech=MockTextToSpeech(),
        wake_matcher=WakeWordMatcher(),
        wake_ack_text="はい？",
        wake_window_seconds=wake_window_seconds,
        vad_threshold=600,
        vad_silence_ms=700,
        vad_max_seconds=12,
        registry=registry,
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_for(relay, None, streams))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"ws://127.0.0.1:{server.server_port}/device/stream", client, stt, registry
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class StreamEndpointTests(unittest.TestCase):
    def test_wake_word_with_command_yields_reply(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with running_relay(["スタックちゃん、こんにちは"]) as (url, client, stt, _):
                spoken = stream_once(url, tone_wav(1.0), timeout=10)
        self.assertEqual(spoken, "こんにちは。")
        self.assertEqual(client.received, ["こんにちは"])
        self.assertEqual(stt.calls, 1)

    def test_speech_without_wake_word_is_dropped(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with running_relay(["今日は寒いね"]) as (url, client, stt, _):
                spoken = stream_once(url, tone_wav(1.0), timeout=4)
        self.assertIsNone(spoken)
        self.assertEqual(client.received, [])
        self.assertEqual(stt.calls, 1)

    def test_wake_word_alone_is_acknowledged_then_next_speech_is_command(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with running_relay(["スタックちゃん", "こんにちは"]) as (url, client, stt, _):
                raw, link = connect(url, timeout=10)
                try:
                    link.send_text(json.dumps({"type": "hello", "device": "test"}))
                    spoken_texts = []
                    for _ in range(2):
                        self._send_utterance(link, tone_wav(1.0))
                        spoken_texts.append(self._await_speak(link))
                finally:
                    link.close()
                    raw.close()
        self.assertEqual(spoken_texts, ["はい？", "こんにちは。"])
        self.assertEqual(client.received, ["こんにちは"])
        self.assertEqual(stt.calls, 2)

    def test_button_press_acts_as_wake_word(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with running_relay(["こんにちは"]) as (url, client, _, _):
                raw, link = connect(url, timeout=10)
                try:
                    link.send_text(json.dumps({"type": "hello", "device": "test"}))
                    link.send_text(json.dumps({"type": "button", "name": "A"}))
                    ack = self._await_speak(link)
                    self._send_utterance(link, tone_wav(1.0))
                    reply = self._await_speak(link)
                finally:
                    link.close()
                    raw.close()
        self.assertEqual([ack, reply], ["はい？", "こんにちは。"])
        self.assertEqual(client.received, ["こんにちは"])

    def test_grok_failure_stays_silent(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with running_relay(["スタックちゃん、失敗して"]) as (url, client, _, _):
                spoken = stream_once(url, tone_wav(1.0), timeout=4)
        self.assertIsNone(spoken)
        self.assertEqual(client.received, ["失敗して"])

    def test_registry_tracks_connection(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with running_relay([]) as (url, _, _, registry):
                raw, link = connect(url, timeout=10)
                try:
                    link.send_text(json.dumps({"type": "hello", "device": "cores3"}))
                    self._await_state(link, "listening")
                    for _ in range(50):
                        if registry.sessions():
                            break
                        threading.Event().wait(0.05)
                    self.assertEqual(len(registry.sessions()), 1)
                finally:
                    link.close()
                    raw.close()
                for _ in range(50):
                    if not registry.sessions():
                        break
                    threading.Event().wait(0.05)
                self.assertEqual(registry.sessions(), [])

    # --- helpers ---

    def _send_utterance(self, link, audio: bytes) -> None:
        for frame in pcm_frames(audio):
            link.send_binary(frame.ljust(FRAME_SAMPLES * 2, b"\0"))
        for _ in range(30):
            link.send_binary(bytes(FRAME_SAMPLES * 2))

    def _await_state(self, link, state: str) -> None:
        for _ in range(50):
            opcode, payload = link.receive()
            if opcode == OP_TEXT and json.loads(payload).get("state") == state:
                return
        self.fail(f"state {state} が届きませんでした。")

    def _await_speak(self, link) -> str:
        expected = 0
        text = ""
        received = bytearray()
        for _ in range(500):
            opcode, payload = link.receive()
            if opcode == OP_TEXT:
                message = json.loads(payload)
                if message.get("type") == "speak":
                    expected = message["bytes"]
                    text = message["text"]
            elif opcode == OP_BINARY and expected:
                received += payload
                if len(received) >= expected:
                    decode_wav(bytes(received))
                    link.send_text(json.dumps({"type": "played"}))
                    return text
        self.fail("speak が届きませんでした。")


class AckCacheTests(unittest.TestCase):
    def test_wake_ack_is_synthesized_once_per_session(self) -> None:
        class CountingTts(MockTextToSpeech):
            calls = 0

            def synthesize(self, text: str) -> bytes:
                CountingTts.calls += 1
                return super().synthesize(text)

        relay = RelayService(client=FakeClient(), max_reply_length=120)
        streams = StreamService(
            relay=relay,
            speech_to_text=ScriptedSpeechToText([]),
            text_to_speech=CountingTts(),
            wake_matcher=WakeWordMatcher(),
            wake_ack_text="はい？",
            wake_window_seconds=8,
            vad_threshold=600,
            vad_silence_ms=700,
            vad_max_seconds=12,
            registry=SessionRegistry(),
        )
        server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_for(relay, None, streams))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                raw, link = connect(f"ws://127.0.0.1:{server.server_port}/device/stream", timeout=10)
                try:
                    link.send_text(json.dumps({"type": "hello", "device": "test"}))
                    for _ in range(3):
                        link.send_text(json.dumps({"type": "button", "name": "A"}))
                        StreamEndpointTests._await_speak(StreamEndpointTests(), link)
                finally:
                    link.close()
                    raw.close()
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(CountingTts.calls, 1)


class SpeechErrorSilenceTests(unittest.TestCase):
    def test_stt_error_is_silent(self) -> None:
        class BrokenStt:
            def transcribe(self, audio: bytes) -> str:
                raise SpeechError("stt_timeout", "時間切れ")

        relay = RelayService(client=FakeClient(), max_reply_length=120)
        streams = StreamService(
            relay=relay,
            speech_to_text=BrokenStt(),
            text_to_speech=MockTextToSpeech(),
            wake_matcher=WakeWordMatcher(),
            wake_ack_text="はい？",
            wake_window_seconds=8,
            vad_threshold=600,
            vad_silence_ms=700,
            vad_max_seconds=12,
            registry=SessionRegistry(),
        )
        server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_for(relay, None, streams))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                spoken = stream_once(
                    f"ws://127.0.0.1:{server.server_port}/device/stream", tone_wav(1.0), timeout=4
                )
        finally:
            server.shutdown()
            server.server_close()
        self.assertIsNone(spoken)
