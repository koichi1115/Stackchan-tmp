import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from stackchan_grok_relay.domain import Utterance
from stackchan_grok_relay.grok import (
    GrokApiClient,
    GrokBotRoutineClient,
    GrokWebhookClient,
    extract_api_text,
    extract_reply,
    looks_like_async_ack,
)
from stackchan_grok_relay.inbox import InboxMessage


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
        mocked_urlopen.return_value = FakeResponse({"status": "accepted", "id": 1})

        result = self.client.reply(Utterance("質問"))

        self.assertEqual(result.error.code, "invalid_response")
        self.assertEqual(mocked_urlopen.call_count, 1)


class ExtractReplyTests(unittest.TestCase):
    def test_accepts_reply_and_other_common_keys(self) -> None:
        self.assertEqual(extract_reply('{"reply": "こんにちは。"}'), "こんにちは。")
        self.assertEqual(extract_reply('{"output": "こんにちは。"}'), "こんにちは。")
        self.assertEqual(extract_reply('{"output": {"text": "こんにちは。"}}'), "こんにちは。")
        self.assertEqual(extract_reply('"こんにちは。"'), "こんにちは。")

    def test_empty_body_or_empty_reply_means_no_reply(self) -> None:
        self.assertEqual(extract_reply(""), "")
        self.assertEqual(extract_reply('{"reply": ""}'), "")

    def test_unknown_shapes_are_none(self) -> None:
        self.assertIsNone(extract_reply("ok"))
        self.assertIsNone(extract_reply('{"status": "accepted", "id": 3}'))
        self.assertIsNone(extract_reply("[1, 2]"))

    @patch("stackchan_grok_relay.grok.urlopen")
    def test_unknown_shape_reports_detail(self, mocked_urlopen) -> None:
        mocked_urlopen.return_value = FakeResponse({"status": "accepted"})
        client = GrokWebhookClient(url="https://example.invalid/webhook", sender_key="k", timeout_seconds=1)
        result = client.reply(Utterance("質問"))
        self.assertEqual(result.error.code, "invalid_response")
        self.assertIn("accepted", result.error.detail)


class AsyncAckTests(unittest.TestCase):
    def test_detects_routine_started_acknowledgements(self) -> None:
        self.assertTrue(looks_like_async_ack('{"success": true, "runUuid": "abc"}'))
        self.assertTrue(looks_like_async_ack('{"status": "accepted"}'))
        self.assertFalse(looks_like_async_ack('{"reply": "こんにちは。"}'))
        self.assertFalse(looks_like_async_ack("ok"))

    @patch("stackchan_grok_relay.grok.urlopen")
    def test_acknowledgement_is_reported_as_async(self, mocked_urlopen) -> None:
        mocked_urlopen.return_value = FakeResponse({"success": True, "runUuid": "abc"})
        client = GrokWebhookClient(url="https://example.invalid/webhook", sender_key="k", timeout_seconds=1)
        result = client.reply(Utterance("質問"))
        self.assertEqual(result.error.code, "invalid_response")
        self.assertIn("async_ack", result.error.detail)


class GrokApiClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = GrokApiClient(api_key="test-only-placeholder", system_prompt="一文で。", timeout_seconds=1)

    def test_extracts_responses_and_chat_completions_shapes(self) -> None:
        responses = {"output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "こんにちは。"}]}]}
        chat = {"choices": [{"message": {"role": "assistant", "content": "こんにちは。"}}]}
        self.assertEqual(extract_api_text(json.dumps(responses)), "こんにちは。")
        self.assertEqual(extract_api_text(json.dumps(chat)), "こんにちは。")
        self.assertEqual(extract_api_text(json.dumps({"output_text": "やあ。"})), "やあ。")
        self.assertIsNone(extract_api_text(json.dumps({"id": "x"})))

    @patch("stackchan_grok_relay.grok.urlopen")
    def test_sends_system_prompt_and_bearer_key(self, mocked_urlopen) -> None:
        mocked_urlopen.return_value = FakeResponse({"output_text": "こんにちは。"})
        result = self.client.reply(Utterance("やあ"))
        request = mocked_urlopen.call_args.args[0]
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(result.reply, "こんにちは。")
        self.assertEqual(body["model"], "grok-4.3")
        self.assertEqual(body["input"][0]["role"], "system")
        self.assertTrue(body["input"][0]["content"].endswith("一文で。"))
        self.assertIn("現在の日時は", body["input"][0]["content"])
        self.assertEqual(body["input"][1], {"role": "user", "content": "やあ"})
        self.assertFalse(body["store"])
        self.assertEqual(request.get_header("Authorization"), "Bearer test-only-placeholder")

    @patch("stackchan_grok_relay.grok.urlopen")
    def test_client_error_is_not_retried(self, mocked_urlopen) -> None:
        mocked_urlopen.side_effect = HTTPError("https://api.x.ai/v1/responses", 401, "unauthorized", None, None)
        result = self.client.reply(Utterance("やあ"))
        self.assertEqual(result.error.code, "http_error")
        self.assertIn("HTTP 401", result.error.detail)
        self.assertEqual(mocked_urlopen.call_count, 1)


class GrokApiToolsTests(unittest.TestCase):
    @patch("stackchan_grok_relay.grok.urlopen")
    def test_tools_and_location_are_sent(self, mocked_urlopen) -> None:
        mocked_urlopen.return_value = FakeResponse({"output_text": "晴れです。"})
        client = GrokApiClient(
            api_key="k", system_prompt="一文で。", timeout_seconds=1, tools=("web_search",), location_hint="東京"
        )
        client.reply(Utterance("天気は？"))
        body = json.loads(mocked_urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(body["tools"], [{"type": "web_search"}])
        system = body["input"][0]["content"]
        self.assertIn("現在の日時は", system)
        self.assertIn("東京", system)
        self.assertIn("検索ツール", system)
        self.assertTrue(system.endswith("一文で。"))

    @patch("stackchan_grok_relay.grok.urlopen")
    def test_without_tools_no_tools_field(self, mocked_urlopen) -> None:
        mocked_urlopen.return_value = FakeResponse({"output_text": "はい。"})
        GrokApiClient(api_key="k", timeout_seconds=1).reply(Utterance("やあ"))
        body = json.loads(mocked_urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertNotIn("tools", body)

    def test_tool_call_items_are_skipped_when_extracting_text(self) -> None:
        payload = {
            "output": [
                {"type": "web_search_call", "status": "completed"},
                {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "晴れです。"}]},
            ]
        }
        self.assertEqual(extract_api_text(json.dumps(payload)), "晴れです。")


class FakeInbox:
    def __init__(self, replies: dict[str, str] | None = None, after_polls: int = 0) -> None:
        self.replies = replies or {}
        self.after_polls = after_polls
        self.polls = 0
        self.acked: list[int] = []
        self.requested: list[str | None] = []

    def fetch(self, after: int = 0, limit: int = 20, reply_to: str | None = None) -> list:
        self.polls += 1
        self.requested.append(reply_to)
        if self.polls <= self.after_polls or reply_to not in self.replies:
            return []
        return [InboxMessage(id=7, text=self.replies[reply_to], created_at=None, reply_to=reply_to)]

    def ack(self, message_id: int) -> None:
        self.acked.append(message_id)


class GrokBotRoutineClientTests(unittest.TestCase):
    def _client(self, inbox, **overrides) -> GrokBotRoutineClient:
        fields = dict(
            url="https://grok.example.invalid/routine",
            sender_key="test-only-placeholder",
            inbox=inbox,
            reply_url="https://inbox.example.invalid/messages",
            timeout_seconds=5.0,
            poll_interval_seconds=0.0,
            sleep=lambda _: None,
        )
        fields.update(overrides)
        return GrokBotRoutineClient(**fields)

    @patch("stackchan_grok_relay.grok.urlopen")
    def test_wakes_routine_then_waits_for_the_matching_reply(self, mocked_urlopen) -> None:
        mocked_urlopen.return_value = FakeResponse({"success": True, "runUuid": "abc"})
        inbox = FakeInbox(after_polls=2)
        client = self._client(inbox)

        def register_reply(*args, **kwargs):
            body = json.loads(mocked_urlopen.call_args.args[0].data.decode("utf-8"))
            inbox.replies[body["reply_to"]] = "晴れです。"
            return FakeResponse({"success": True})

        mocked_urlopen.side_effect = register_reply
        result = client.reply(Utterance("天気は？"))

        self.assertEqual(result.reply, "晴れです。")
        self.assertIsNone(result.error)
        body = json.loads(mocked_urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(body["text"], "天気は？")
        self.assertEqual(body["reply_url"], "https://inbox.example.invalid/messages")
        self.assertRegex(body["reply_to"], r"^[A-Za-z0-9_-]{8,64}$")
        self.assertEqual(inbox.acked, [7])
        self.assertTrue(all(r == body["reply_to"] for r in inbox.requested))

    @patch("stackchan_grok_relay.grok.urlopen")
    def test_times_out_when_no_reply_arrives(self, mocked_urlopen) -> None:
        mocked_urlopen.return_value = FakeResponse({"success": True})
        ticks = iter([0.0, 0.0, 1.0, 2.0, 3.0, 6.0, 7.0, 8.0])
        client = self._client(FakeInbox(), clock=lambda: next(ticks))
        result = client.reply(Utterance("やあ"))
        self.assertEqual(result.reply, "")
        self.assertEqual(result.error.code, "routine_timeout")

    @patch("stackchan_grok_relay.grok.urlopen")
    def test_routine_off_is_reported_as_http_error(self, mocked_urlopen) -> None:
        mocked_urlopen.side_effect = HTTPError("https://grok.example.invalid/routine", 400, "bad request", None, None)
        inbox = FakeInbox()
        result = self._client(inbox).reply(Utterance("やあ"))
        self.assertEqual(result.error.code, "http_error")
        self.assertIn("HTTP 400", result.error.detail)
        self.assertEqual(inbox.polls, 0)


if __name__ == "__main__":
    unittest.main()
