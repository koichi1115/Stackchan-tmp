from dataclasses import dataclass
import json
import socket
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .domain import Utterance


MAX_RESPONSE_BYTES = 65_536


@dataclass(frozen=True)
class GrokError:
    code: str
    message: str
    retryable: bool
    attempts: int


@dataclass(frozen=True)
class GrokResult:
    reply: str
    error: GrokError | None = None


@dataclass(frozen=True)
class GrokWebhookClient:
    url: str
    sender_key: str
    timeout_seconds: float

    def reply(self, utterance: Utterance) -> GrokResult:
        payload = json.dumps(
            {"text": utterance.text},
            ensure_ascii=False,
        ).encode("utf-8")
        request = Request(
            self.url,
            data=payload,
            headers=self._headers(),
            method="POST",
        )

        for attempt in range(1, 3):
            try:
                return self._send(request)
            except HTTPError as error:
                retryable = 500 <= error.code < 600
                if retryable and attempt == 1:
                    continue
                return _failure("http_error", "Webhook が HTTP エラーを返しました。", retryable, attempt)
            except (TimeoutError, socket.timeout):
                if attempt == 1:
                    continue
                return _failure("timeout", "Webhook が時間内に応答しませんでした。", True, attempt)
            except URLError:
                if attempt == 1:
                    continue
                return _failure("network_error", "Webhook に接続できませんでした。", True, attempt)
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
                return _failure("invalid_response", "Webhook の応答形式が不正です。", False, attempt)

        return _failure("unknown_error", "Webhook の呼び出しに失敗しました。", False, 2)

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": "Bearer " + self.sender_key,
            "X-Automation-Key": self.sender_key,
        }

    def _send(self, request: Request) -> GrokResult:
        with urlopen(request, timeout=self.timeout_seconds) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise ValueError("response_too_large")

        decoded = json.loads(body.decode("utf-8"))
        if not isinstance(decoded, dict) or not isinstance(decoded.get("reply"), str):
            raise ValueError("missing_reply")
        return GrokResult(reply=decoded["reply"])


def _failure(code: str, message: str, retryable: bool, attempts: int) -> GrokResult:
    return GrokResult(
        reply="",
        error=GrokError(
            code=code,
            message=message,
            retryable=retryable,
            attempts=attempts,
        ),
    )
