from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import sys
from typing import Protocol

from .config import Config
from .domain import SpokenReply, Utterance, clamp_reply, parse_utterance
from .grok import GrokResult, GrokWebhookClient


MAX_REQUEST_BYTES = 8_192


class ReplyClient(Protocol):
    def reply(self, utterance: Utterance) -> GrokResult:
        ...


@dataclass(frozen=True)
class RelayService:
    client: ReplyClient
    max_reply_length: int

    def handle(self, body: object) -> SpokenReply:
        utterance = parse_utterance(body)
        result: GrokResult = self.client.reply(utterance)
        if result.error:
            print(
                json.dumps(
                    {
                        "event": "grok_webhook_error",
                        "code": result.error.code,
                        "attempts": result.error.attempts,
                        "message": result.error.message,
                    },
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )
            return SpokenReply(speak="")
        return SpokenReply(speak=clamp_reply(result.reply, self.max_reply_length))


def build_server(config: Config) -> ThreadingHTTPServer:
    client = GrokWebhookClient(
        url=config.webhook_url,
        sender_key=config.webhook_sender_key,
        timeout_seconds=config.timeout_seconds,
    )
    service = RelayService(client=client, max_reply_length=config.max_reply_length)
    return ThreadingHTTPServer((config.host, config.port), _handler_for(service))


def _handler_for(service: RelayService) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self._send(
                405,
                {"error": {"code": "method_not_allowed", "message": "POST を使用してください。"}},
            )

        def do_POST(self) -> None:
            if self.path != "/utterance":
                self._send(404, {"error": {"code": "not_found", "message": "パスが見つかりません。"}})
                return

            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > MAX_REQUEST_BYTES:
                    raise ValueError("リクエストの大きさが不正です。")
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                reply = service.handle(body)
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as error:
                self._send(
                    400,
                    {"error": {"code": "invalid_request", "message": str(error)}},
                )
                return

            self._send(200, {"speak": reply.speak})

        def _send(self, status: int, body: object) -> None:
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            return

    return Handler
