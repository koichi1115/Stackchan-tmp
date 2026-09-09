import os
import unittest
from unittest.mock import patch

from stackchan_grok_relay.config import Config, ConfigError


class ConfigTests(unittest.TestCase):
    @patch.dict(
        os.environ,
        {
            "GROK_WEBHOOK_URL": "https://example.invalid/webhook",
            "GROK_WEBHOOK_SENDER_KEY": "test-only-placeholder",
        },
        clear=True,
    )
    def test_defaults_to_loopback(self) -> None:
        self.assertEqual(Config.from_env().host, "127.0.0.1")

    @patch.dict(
        os.environ,
        {
            "GROK_WEBHOOK_URL": "https://example.invalid/webhook",
            "GROK_WEBHOOK_SENDER_KEY": "test-only-placeholder",
            "RELAY_HOST": "0.0.0.0",
        },
        clear=True,
    )
    def test_rejects_all_interfaces(self) -> None:
        with self.assertRaises(ConfigError):
            Config.from_env()

    @patch.dict(
        os.environ,
        {
            "GROK_WEBHOOK_URL": "https://example.invalid/webhook",
            "GROK_WEBHOOK_SENDER_KEY": "test-only-placeholder",
            "RELAY_HOST": "8.8.8.8",
        },
        clear=True,
    )
    def test_rejects_public_address(self) -> None:
        with self.assertRaises(ConfigError):
            Config.from_env()

    @patch.dict(
        os.environ,
        {
            "GROK_WEBHOOK_URL": "https://example.invalid/webhook",
            "GROK_WEBHOOK_SENDER_KEY": "test-only-placeholder",
            "RELAY_HOST": "100.64.0.1",
        },
        clear=True,
    )
    def test_accepts_tailscale_address(self) -> None:
        self.assertEqual(Config.from_env().host, "100.64.0.1")

    @patch.dict(
        os.environ,
        {"GROK_WEBHOOK_URL": "https://example.invalid/webhook"},
        clear=True,
    )
    def test_requires_sender_key(self) -> None:
        with self.assertRaises(ConfigError):
            Config.from_env()


BASE_ENV = {
    "GROK_WEBHOOK_URL": "https://example.invalid/webhook",
    "GROK_WEBHOOK_SENDER_KEY": "test-only-placeholder",
}


class SpeechConfigTests(unittest.TestCase):
    @patch.dict(os.environ, BASE_ENV, clear=True)
    def test_defaults_to_mock_engines(self) -> None:
        config = Config.from_env()

        self.assertEqual(config.stt_engine, "mock")
        self.assertEqual(config.tts_engine, "mock")
        self.assertEqual(config.mock_transcript, "こんにちは")

    @patch.dict(os.environ, {**BASE_ENV, "STT_ENGINE": "whisper.cpp"}, clear=True)
    def test_rejects_unknown_stt_engine(self) -> None:
        with self.assertRaises(ConfigError):
            Config.from_env()

    @patch.dict(os.environ, {**BASE_ENV, "TTS_ENGINE": "unknown"}, clear=True)
    def test_rejects_unknown_tts_engine(self) -> None:
        with self.assertRaises(ConfigError):
            Config.from_env()

    @patch.dict(os.environ, {**BASE_ENV, "STT_ENGINE": "whisper_http"}, clear=True)
    def test_real_stt_engine_requires_url(self) -> None:
        with self.assertRaises(ConfigError):
            Config.from_env()

    @patch.dict(os.environ, {**BASE_ENV, "TTS_ENGINE": "voicevox_http"}, clear=True)
    def test_real_tts_engine_requires_url(self) -> None:
        with self.assertRaises(ConfigError):
            Config.from_env()

    @patch.dict(
        os.environ,
        {
            **BASE_ENV,
            "STT_ENGINE": "whisper_http",
            "STT_URL": "http://127.0.0.1:8080/inference",
            "TTS_ENGINE": "voicevox_http",
            "TTS_URL": "http://127.0.0.1:50021",
            "TTS_SPEAKER_ID": "3",
        },
        clear=True,
    )
    def test_accepts_real_engines_with_urls(self) -> None:
        config = Config.from_env()

        self.assertEqual(config.stt_url, "http://127.0.0.1:8080/inference")
        self.assertEqual(config.tts_url, "http://127.0.0.1:50021")
        self.assertEqual(config.tts_speaker_id, 3)

    @patch.dict(os.environ, {**BASE_ENV, "MAX_AUDIO_BYTES": "16"}, clear=True)
    def test_rejects_tiny_audio_limit(self) -> None:
        with self.assertRaises(ConfigError):
            Config.from_env()


if __name__ == "__main__":
    unittest.main()
