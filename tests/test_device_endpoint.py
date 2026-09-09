import base64
import contextlib
import io
import json
from http.server import ThreadingHTTPServer
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from stackchan_grok_relay.device import SPOKEN_TEXT_HEADER, DeviceAudioService
from stackchan_grok_relay.grok import GrokError, GrokResult
from stackchan_grok_relay.relay import RelayService, _handler_for
from stackchan_grok_relay.speech import MockSpeechToText, MockTextToSpeech
from stackchan_grok_relay.wav import decode_wav, tone_wav

from test_speech import silent_wav


class FakeClient:
    def reply(self, utterance) -> GrokResult:
        if utterance.text == "こんにちは":
            return GrokResult(reply="こんにちは。二文目です。")
        return GrokResult(
            reply="",
            error=GrokError(code="timeout", message="時間切れです。", retryable=True, attempts=2),
        )


@contextlib.contextmanager
def running_relay():
    relay = RelayService(client=FakeClient(), max_reply_length=120)
    device = DeviceAudioService(
        relay=relay,
        speech_to_text=MockSpeechToText(transcript="こんにちは"),
        text_to_speech=MockTextToSpeech(),
        max_audio_bytes=1_000_000,
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_for(relay, device))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def post_audio(base_url: str, audio: bytes):
    request = Request(
        f"{base_url}/device/utterance",
        data=audio,
        headers={"Content-Type": "audio/wav"},
        method="POST",
    )
    return urlopen(request, timeout=5)


class DeviceEndpointTests(unittest.TestCase):
    def test_returns_wav_and_spoken_text_header(self) -> None:
        with running_relay() as base_url:
            with post_audio(base_url, tone_wav(0.2)) as response:
                status = response.status
                content_type = response.headers.get("Content-Type")
                spoken = response.headers.get(SPOKEN_TEXT_HEADER)
                body = response.read()

        self.assertEqual(status, 200)
        self.assertEqual(content_type, "audio/wav")
        self.assertEqual(base64.b64decode(spoken).decode("utf-8"), "こんにちは。")
        self.assertGreater(decode_wav(body).frame_count, 0)

    def test_recording_without_speech_returns_silence(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with running_relay() as base_url:
                with post_audio(base_url, silent_wav()) as response:
                    status = response.status
                    body = response.read()
                    spoken = response.headers.get(SPOKEN_TEXT_HEADER)

        self.assertEqual(status, 204)
        self.assertEqual(body, b"")
        self.assertIsNone(spoken)

    def test_webhook_error_returns_silence(self) -> None:
        relay = RelayService(client=FakeClient(), max_reply_length=120)
        device = DeviceAudioService(
            relay=relay,
            speech_to_text=MockSpeechToText(transcript="webhook を失敗させる発話"),
            text_to_speech=MockTextToSpeech(),
            max_audio_bytes=1_000_000,
        )
        server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_for(relay, device))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                with post_audio(f"http://127.0.0.1:{server.server_port}", tone_wav(0.2)) as response:
                    status = response.status
                    body = response.read()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertEqual(status, 204)
        self.assertEqual(body, b"")

    def test_rejects_empty_body(self) -> None:
        with running_relay() as base_url:
            request = Request(
                f"{base_url}/device/utterance",
                data=b"",
                headers={"Content-Type": "audio/wav"},
                method="POST",
            )
            with self.assertRaises(HTTPError) as raised:
                urlopen(request, timeout=5)
        self.assertEqual(raised.exception.code, 400)

    def test_rejects_oversized_body(self) -> None:
        relay = RelayService(client=FakeClient(), max_reply_length=120)
        device = DeviceAudioService(
            relay=relay,
            speech_to_text=MockSpeechToText(transcript="こんにちは"),
            text_to_speech=MockTextToSpeech(),
            max_audio_bytes=1_024,
        )
        server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_for(relay, device))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = Request(
                f"http://127.0.0.1:{server.server_port}/device/utterance",
                data=tone_wav(0.5),
                headers={"Content-Type": "audio/wav"},
                method="POST",
            )
            with self.assertRaises(HTTPError) as raised:
                urlopen(request, timeout=5)
            self.assertEqual(raised.exception.code, 400)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_utterance_endpoint_is_unchanged(self) -> None:
        with running_relay() as base_url:
            request = Request(
                f"{base_url}/utterance",
                data=json.dumps({"text": "こんにちは"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request, timeout=5) as response:
                body = json.loads(response.read().decode("utf-8"))

        self.assertEqual(body, {"speak": "こんにちは。"})

    def test_device_path_is_absent_without_device_service(self) -> None:
        relay = RelayService(client=FakeClient(), max_reply_length=120)
        server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_for(relay))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = Request(
                f"http://127.0.0.1:{server.server_port}/device/utterance",
                data=tone_wav(0.1),
                headers={"Content-Type": "audio/wav"},
                method="POST",
            )
            with self.assertRaises(HTTPError) as raised:
                urlopen(request, timeout=5)
            self.assertEqual(raised.exception.code, 404)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
