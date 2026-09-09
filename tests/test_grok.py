import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from stackchan_grok_relay.domain import Utterance
from stackchan_grok_relay.grok import GrokWebhookClient


class FakeResponse:
    def __init__(self, body: object) -> None:
        self.body = json.dumps(body, ensure_ascii=False).encode("utf-8")

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, size: int) -> bytes:
        return self.body


class GrokWebhookClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = GrokWebhookClient(
            url="https://example.invalid/webhook",
            sender_key="test-only-placeholder",
            timeout_seconds=1,
        )

    @patch("stackchan_grok_relay.grok.urlopen")
    def test_sends_only_text_and_sender_key_headers(self, mocked_urlopen) -> None:
        mocked_urlopen.return_value = FakeResponse({"reply": "返答です。"})

        self.client.reply(Utterance("質問"))

        request = mocked_urlopen.call_args.args[0]
        self.assertEqual(json.loads(request.data.decode("utf-8")), {"text": "質問"})
        self.assertEqual(request.get_header("Authorization"), "Bearer test-only-placeholder")
        self.assertEqual(request.get_header("X-automation-key"), "test-only-placeholder")

    @patch("stackchan_grok_relay.grok.urlopen")
    def test_retries_once_after_server_error(self, mocked_urlopen) -> None:
        mocked_urlopen.side_effect = [
            HTTPError(self.client.url, 503, "unavailable", None, None),
            FakeResponse({"reply": "返答です。"}),
        ]

        result = self.client.reply(Utterance("質問"))

        self.assertEqual(result.reply, "返答です。")
        self.assertIsNone(result.error)
        self.assertEqual(mocked_urlopen.call_count, 2)

    @patch("stackchan_grok_relay.grok.urlopen", side_effect=TimeoutError)
    def test_timeout_returns_structured_error_after_one_retry(self, mocked_urlopen) -> None:
        result = self.client.reply(Utterance("質問"))

        self.assertEqual(result.reply, "")
        self.assertIsNotNone(result.error)
        self.assertEqual(result.error.code, "timeout")
        self.assertEqual(result.error.attempts, 2)
        self.assertEqual(mocked_urlopen.call_count, 2)

    @patch("stackchan_grok_relay.grok.urlopen")
    def test_invalid_response_is_not_retried(self, mocked_urlopen) -> None:
        mocked_urlopen.return_value = FakeResponse({"text": "形式違い"})

        result = self.client.reply(Utterance("質問"))

        self.assertEqual(result.error.code, "invalid_response")
        self.assertEqual(mocked_urlopen.call_count, 1)


if __name__ == "__main__":
    unittest.main()
