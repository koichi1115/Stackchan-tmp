from http.server import ThreadingHTTPServer
import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from stackchan_grok_relay.grok import GrokError, GrokResult
from stackchan_grok_relay.relay import RelayService, _handler_for


class FakeClient:
    def __init__(self, result: GrokResult) -> None:
        self.result = result

    def reply(self, utterance) -> GrokResult:
        return self.result


class RelayTests(unittest.TestCase):
    def test_webhook_error_becomes_silence(self) -> None:
        service = RelayService(
            client=FakeClient(
                GrokResult(
                    reply="",
                    error=GrokError(
                        code="timeout",
                        message="時間切れです。",
                        retryable=True,
                        attempts=2,
                    ),
                )
            ),
            max_reply_length=120,
        )

        self.assertEqual(service.handle({"text": "質問"}).speak, "")

    def test_utterance_endpoint_returns_clamped_reply(self) -> None:
        service = RelayService(
            client=FakeClient(GrokResult(reply="一文目です。二文目です。")),
            max_reply_length=120,
        )
        server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_for(service))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = Request(
                f"http://127.0.0.1:{server.server_port}/utterance",
                data=json.dumps({"text": "質問"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request, timeout=2) as response:
                body = json.loads(response.read().decode("utf-8"))
            self.assertEqual(body, {"speak": "一文目です。"})
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_rejects_invalid_body(self) -> None:
        service = RelayService(
            client=FakeClient(GrokResult(reply="返答です。")),
            max_reply_length=120,
        )
        server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_for(service))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = Request(
                f"http://127.0.0.1:{server.server_port}/utterance",
                data=b"{}",
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(HTTPError) as raised:
                urlopen(request, timeout=2)
            self.assertEqual(raised.exception.code, 400)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
