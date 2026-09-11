import unittest
from unittest import mock

from stackchan_grok_relay import speech
from stackchan_grok_relay.speech import (
    MockSpeechToText,
    MockTextToSpeech,
    SpeechError,
    VoicevoxHttpTextToSpeech,
    WhisperHttpSpeechToText,
    build_speech_to_text,
    build_text_to_speech,
)
from stackchan_grok_relay.wav import (
    DEVICE_CHANNELS,
    DEVICE_SAMPLE_RATE,
    DEVICE_SAMPLE_WIDTH,
    WavAudio,
    WavError,
    decode_wav,
    encode_wav,
    tone_wav,
)


def silent_wav() -> bytes:
    return encode_wav(
        WavAudio(
            sample_rate=DEVICE_SAMPLE_RATE,
            channels=DEVICE_CHANNELS,
            sample_width=DEVICE_SAMPLE_WIDTH,
            frames=b"",
        )
    )


class MockSpeechToTextTests(unittest.TestCase):
    def test_transcribes_valid_recording(self) -> None:
        engine = MockSpeechToText(transcript="こんにちは")

        self.assertEqual(engine.transcribe(tone_wav(0.2)), "こんにちは")

    def test_recording_without_frames_is_empty_transcript(self) -> None:
        engine = MockSpeechToText(transcript="こんにちは")

        self.assertEqual(engine.transcribe(silent_wav()), "")

    def test_rejects_non_wav_payload(self) -> None:
        engine = MockSpeechToText(transcript="こんにちは")

        with self.assertRaises(SpeechError) as raised:
            engine.transcribe(b"not-a-wav-file")
        self.assertEqual(raised.exception.code, "invalid_audio")


class MockTextToSpeechTests(unittest.TestCase):
    def test_returns_playable_wav(self) -> None:
        audio = MockTextToSpeech().synthesize("こんにちは。")

        decoded = decode_wav(audio)
        self.assertEqual(decoded.sample_rate, DEVICE_SAMPLE_RATE)
        self.assertEqual(decoded.channels, DEVICE_CHANNELS)
        self.assertGreater(decoded.frame_count, 0)

    def test_is_deterministic(self) -> None:
        engine = MockTextToSpeech()

        self.assertEqual(engine.synthesize("こんにちは。"), engine.synthesize("こんにちは。"))

    def test_rejects_empty_text(self) -> None:
        with self.assertRaises(SpeechError):
            MockTextToSpeech().synthesize("")


class WavTests(unittest.TestCase):
    def test_round_trip_preserves_frames(self) -> None:
        original = WavAudio(
            sample_rate=DEVICE_SAMPLE_RATE,
            channels=DEVICE_CHANNELS,
            sample_width=DEVICE_SAMPLE_WIDTH,
            frames=b"\x01\x02\x03\x04",
        )

        self.assertEqual(decode_wav(encode_wav(original)), original)

    def test_rejects_garbage(self) -> None:
        with self.assertRaises(WavError):
            decode_wav(b"")


class EngineSelectionTests(unittest.TestCase):
    def test_builds_mock_engines(self) -> None:
        stt = build_speech_to_text(
            engine="mock", url="", timeout_seconds=1.0, mock_transcript="こんにちは"
        )
        tts = build_text_to_speech(engine="mock", url="", speaker_id=1, timeout_seconds=1.0)

        self.assertIsInstance(stt, MockSpeechToText)
        self.assertIsInstance(tts, MockTextToSpeech)

    def test_builds_single_real_engines(self) -> None:
        stt = build_speech_to_text(
            engine="whisper_http",
            url="http://127.0.0.1:8080/inference",
            timeout_seconds=1.0,
            mock_transcript="こんにちは",
        )
        tts = build_text_to_speech(
            engine="voicevox_http",
            url="http://127.0.0.1:50021",
            speaker_id=3,
            timeout_seconds=1.0,
        )

        self.assertIsInstance(stt, WhisperHttpSpeechToText)
        self.assertIsInstance(tts, VoicevoxHttpTextToSpeech)

    def test_rejects_unknown_engines(self) -> None:
        with self.assertRaises(ValueError):
            build_speech_to_text(
                engine="unknown", url="", timeout_seconds=1.0, mock_transcript=""
            )
        with self.assertRaises(ValueError):
            build_text_to_speech(engine="unknown", url="", speaker_id=1, timeout_seconds=1.0)


class WhisperPromptTests(unittest.TestCase):
    """whisper へ渡す初期プロンプト。無いとウェイクワードが別語に化けます。"""

    def _body(self, prompt: str) -> bytes:
        engine = WhisperHttpSpeechToText(
            url="http://127.0.0.1:1/inference", timeout_seconds=0.1, prompt=prompt
        )
        captured: dict[str, bytes] = {}

        def fake_post(request, timeout_seconds, stage):
            captured["body"] = request.data
            return {"text": "スタックちゃん"}

        with mock.patch.object(speech, "_post_json", fake_post):
            engine.transcribe(b"RIFF----WAVEfmt ")
        return captured["body"]

    def test_sends_the_prompt_as_a_form_field(self) -> None:
        body = self._body("スタックちゃん")

        self.assertIn(b'name="prompt"', body)
        self.assertIn("スタックちゃん".encode("utf-8"), body)

    def test_omits_the_field_when_the_prompt_is_empty(self) -> None:
        body = self._body("")

        self.assertNotIn(b'name="prompt"', body)

    def test_body_stays_a_well_formed_multipart(self) -> None:
        body = self._body("スタックちゃん")

        self.assertTrue(body.endswith(b"--\r\n"))
        self.assertEqual(body.count(b'Content-Disposition: form-data; name="file"'), 1)
        self.assertEqual(body.count(b'name="response_format"'), 1)


class RealEngineFailureTests(unittest.TestCase):
    def test_unreachable_stt_becomes_speech_error(self) -> None:
        engine = WhisperHttpSpeechToText(
            url="http://127.0.0.1:1/inference", timeout_seconds=0.5
        )

        with self.assertRaises(SpeechError) as raised:
            engine.transcribe(tone_wav(0.1))
        self.assertTrue(raised.exception.code.startswith("stt_"))

    def test_unreachable_tts_becomes_speech_error(self) -> None:
        engine = VoicevoxHttpTextToSpeech(
            url="http://127.0.0.1:1", speaker_id=1, timeout_seconds=0.5
        )

        with self.assertRaises(SpeechError) as raised:
            engine.synthesize("こんにちは。")
        self.assertTrue(raised.exception.code.startswith("tts_"))


if __name__ == "__main__":
    unittest.main()
