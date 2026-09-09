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


if __name__ == "__main__":
    unittest.main()
