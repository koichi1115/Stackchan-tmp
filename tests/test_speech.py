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


class VoicevoxScaleTests(unittest.TestCase):
    """audio_query の結果に speedScale / volumeScale を足してから synthesis へ渡すことを確かめます。"""

    def _run(self, engine: VoicevoxHttpTextToSpeech) -> dict:
        import io
        import json as _json

        captured: dict = {}

        class FakeResponse(io.BytesIO):
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

        def fake_urlopen(request, timeout):
            if "/audio_query" in request.full_url:
                return FakeResponse(_json.dumps({"accent_phrases": [], "speedScale": 1.0}).encode("utf-8"))
            captured["synthesis_body"] = _json.loads(request.data.decode("utf-8"))
            return FakeResponse(tone_wav(0.1))

        with mock.patch.object(speech, "urlopen", fake_urlopen):
            engine.synthesize("はい？")
        return captured["synthesis_body"]

    def test_default_scales_leave_the_query_untouched(self) -> None:
        body = self._run(VoicevoxHttpTextToSpeech(url="http://127.0.0.1:50021", speaker_id=1, timeout_seconds=1))
        self.assertEqual(body["speedScale"], 1.0)
        self.assertNotIn("volumeScale", body)

    def test_custom_scales_are_applied(self) -> None:
        body = self._run(
            VoicevoxHttpTextToSpeech(
                url="http://127.0.0.1:50021", speaker_id=1, timeout_seconds=1, speed_scale=1.2, volume_scale=1.5
            )
        )
        self.assertEqual(body["speedScale"], 1.2)
        self.assertEqual(body["volumeScale"], 1.5)

    def test_builder_passes_scales(self) -> None:
        tts = build_text_to_speech(
            engine="voicevox_http", url="http://127.0.0.1:50021", speaker_id=3, timeout_seconds=1,
            speed_scale=1.3, volume_scale=0.8,
        )
        self.assertIsInstance(tts, VoicevoxHttpTextToSpeech)
        self.assertEqual(tts.speed_scale, 1.3)
        self.assertEqual(tts.volume_scale, 0.8)

