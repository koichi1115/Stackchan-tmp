import contextlib
import io
import unittest

from stackchan_grok_relay.device import DeviceAudioService
from stackchan_grok_relay.grok import GrokError, GrokResult
from stackchan_grok_relay.relay import RelayService
from stackchan_grok_relay.speech import MockSpeechToText, MockTextToSpeech, SpeechError
from stackchan_grok_relay.wav import decode_wav, tone_wav

from test_speech import silent_wav


class FakeClient:
    def __init__(self, result: GrokResult) -> None:
        self.result = result

    def reply(self, utterance) -> GrokResult:
        return self.result


class FailingSpeechToText:
    def transcribe(self, audio: bytes) -> str:
        raise SpeechError("stt_timeout", "音声エンジンが時間内に応答しませんでした。")


class FailingTextToSpeech:
    def synthesize(self, text: str) -> bytes:
        raise SpeechError("tts_network_error", "音声エンジンに接続できませんでした。")


def build_service(
    result: GrokResult,
    speech_to_text=None,
    text_to_speech=None,
) -> DeviceAudioService:
    return DeviceAudioService(
        relay=RelayService(client=FakeClient(result), max_reply_length=120),
        speech_to_text=speech_to_text or MockSpeechToText(transcript="こんにちは"),
        text_to_speech=text_to_speech or MockTextToSpeech(),
        max_audio_bytes=1_000_000,
    )


@contextlib.contextmanager
def captured_stderr():
    buffer = io.StringIO()
    with contextlib.redirect_stderr(buffer):
        yield buffer


class DeviceAudioServiceTests(unittest.TestCase):
    def test_returns_clamped_sentence_and_playable_audio(self) -> None:
        service = build_service(GrokResult(reply="こんにちは。二文目です。"))

        reply = service.handle(tone_wav(0.2))

        self.assertEqual(reply.text, "こんにちは。")
        self.assertFalse(reply.is_silent)
        self.assertGreater(decode_wav(reply.audio).frame_count, 0)

    def test_webhook_error_is_silence(self) -> None:
        service = build_service(
            GrokResult(
                reply="",
                error=GrokError(code="timeout", message="時間切れです。", retryable=True, attempts=2),
            )
        )

        with captured_stderr():
            reply = service.handle(tone_wav(0.2))

        self.assertTrue(reply.is_silent)
        self.assertEqual(reply.text, "")

    def test_empty_reply_is_silence(self) -> None:
        service = build_service(GrokResult(reply="   "))

        reply = service.handle(tone_wav(0.2))

        self.assertTrue(reply.is_silent)

    def test_recording_without_speech_is_silence(self) -> None:
        service = build_service(GrokResult(reply="こんにちは。"))

        with captured_stderr() as errors:
            reply = service.handle(silent_wav())

        self.assertTrue(reply.is_silent)
        self.assertIn("empty_transcript", errors.getvalue())

    def test_stt_failure_is_silence(self) -> None:
        service = build_service(
            GrokResult(reply="こんにちは。"), speech_to_text=FailingSpeechToText()
        )

        with captured_stderr() as errors:
            reply = service.handle(tone_wav(0.2))

        self.assertTrue(reply.is_silent)
        self.assertIn("stt_timeout", errors.getvalue())

    def test_tts_failure_is_silence(self) -> None:
        service = build_service(
            GrokResult(reply="こんにちは。"), text_to_speech=FailingTextToSpeech()
        )

        with captured_stderr() as errors:
            reply = service.handle(tone_wav(0.2))

        self.assertTrue(reply.is_silent)
        self.assertIn("tts_network_error", errors.getvalue())

    def test_oversized_recording_is_silence(self) -> None:
        service = build_service(GrokResult(reply="こんにちは。"))
        oversized = DeviceAudioService(
            relay=service.relay,
            speech_to_text=service.speech_to_text,
            text_to_speech=service.text_to_speech,
            max_audio_bytes=16,
        )

        with captured_stderr():
            reply = oversized.handle(tone_wav(0.2))

        self.assertTrue(reply.is_silent)

    def test_empty_body_is_silence(self) -> None:
        service = build_service(GrokResult(reply="こんにちは。"))

        with captured_stderr():
            reply = service.handle(b"")

        self.assertTrue(reply.is_silent)


if __name__ == "__main__":
    unittest.main()
