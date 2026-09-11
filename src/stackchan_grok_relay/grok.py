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
    detail: str = ""  # 診断用。HTTP ステータスや応答本文の先頭（鍵は含めない）


# 返答の本文として受け付けるキー。Grok routine 側の応答形式が固定でないため広めに拾う。
REPLY_KEYS = ("reply", "speak", "text", "output", "response", "message", "result", "content", "answer")
DETAIL_LIMIT = 300


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
                return _failure(
                    "http_error",
                    "Webhook が HTTP エラーを返しました。",
                    retryable,
                    attempt,
                    detail=f"HTTP {error.code} " + _snippet(_read_error_body(error)),
                )
            except (TimeoutError, socket.timeout):
                if attempt == 1:
                    continue
                return _failure("timeout", "Webhook が時間内に応答しませんでした。", True, attempt)
            except URLError:
                if attempt == 1:
                    continue
                return _failure("network_error", "Webhook に接続できませんでした。", True, attempt)
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as error:
                return _failure(
                    "invalid_response",
                    "Webhook の応答形式が不正です。",
                    False,
                    attempt,
                    detail=str(error),
                )

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

        status = getattr(response, "status", None)
        text = body.decode("utf-8")
        reply = extract_reply(text)
        if reply is None:
            raise ValueError(f"missing_reply: HTTP {status} " + _snippet(text))
        return GrokResult(reply=reply)


def extract_reply(text: str) -> str | None:
    """応答本文から発話文を取り出します。取り出せない形なら None です。

    受け付ける形: {"reply": "..."} をはじめ REPLY_KEYS のいずれかが文字列、
    入れ子（{"output": {"text": "..."}} など）、または本文全体が JSON 文字列。
    空文字列は「返答なし」として空文字列のまま返します。
    """
    stripped = text.strip()
    if not stripped:
        return ""
    try:
        decoded = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return _reply_from(decoded, depth=0)


def _reply_from(value: object, depth: int) -> str | None:
    if isinstance(value, str):
        return value
    if depth >= 3 or not isinstance(value, dict):
        return None
    for key in REPLY_KEYS:
        if key in value:
            found = _reply_from(value[key], depth + 1)
            if found is not None:
                return found
    return None


def _read_error_body(error: HTTPError) -> str:
    try:
        return error.read(4_096).decode("utf-8", "replace")
    except (OSError, ValueError, AttributeError):
        return ""


def _snippet(text: str) -> str:
    compact = " ".join(text.split())
    return compact[:DETAIL_LIMIT]


def _failure(code: str, message: str, retryable: bool, attempts: int, detail: str = "") -> GrokResult:
    return GrokResult(
        reply="",
        error=GrokError(
            code=code,
            message=message,
            retryable=retryable,
            attempts=attempts,
            detail=detail,
        ),
    )
