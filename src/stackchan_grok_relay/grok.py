from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import secrets
import socket
import time
from typing import Callable
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
            if looks_like_async_ack(text):
                raise ValueError(
                    "async_ack: Webhook は routine の起動確認だけを返しました（返答は同期では届きません）。"
                    " REPLY_ENGINE=grok_api を使うか、routine の出力を受信箱へ投函させてください。 "
                    + _snippet(text)
                )
            raise ValueError(f"missing_reply: HTTP {status} " + _snippet(text))
        return GrokResult(reply=reply)


ASYNC_ACK_KEYS = ("runuuid", "run_id", "runid", "run", "accepted", "queued", "job_id", "jobid")


def looks_like_async_ack(text: str) -> bool:
    """{"success":true,"runUuid":"..."} のような「起動しました」応答かを見ます。"""
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        return False
    if not isinstance(decoded, dict):
        return False
    keys = {str(key).lower() for key in decoded}
    if keys & set(ASYNC_ACK_KEYS):
        return True
    status = str(decoded.get("status", "")).lower()
    return status in ("accepted", "queued", "started", "running")


class ReplyInbox:
    """GrokBotRoutineClient が返事を受け取るのに使う受信箱の最小インターフェース（InboxClient が満たす）。"""

    def fetch(self, after: int = 0, limit: int = 20, reply_to: str | None = None) -> list:  # pragma: no cover
        ...

    def ack(self, message_id: int) -> None:  # pragma: no cover
        ...


@dataclass(frozen=True)
class GrokBotRoutineClient:
    """Grok Bot の routine を Webhook で起こし、返事を受信箱で待ちます。

    Webhook は「起動しました」を返すだけの非同期型なので、routine 側に
    「reply_url へ {"text": 返事, "reply_to": 照合 ID} を送信キー付きで POST する」と教えておき、
    リレーはその照合 ID で受信箱を絞って取り出します（worker/GROK_ROUTINE.md）。
    """

    url: str
    sender_key: str
    inbox: ReplyInbox
    reply_url: str
    timeout_seconds: float = 40.0  # 返事を待つ上限。routine の実行はそれなりに時間がかかる
    webhook_timeout_seconds: float = 10.0
    poll_interval_seconds: float = 1.0
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep

    def reply(self, utterance: Utterance) -> GrokResult:
        reply_to = secrets.token_urlsafe(9)  # [A-Za-z0-9_-] の 12 文字
        payload = json.dumps(
            {"text": utterance.text, "reply_to": reply_to, "reply_url": self.reply_url},
            ensure_ascii=False,
        ).encode("utf-8")
        request = Request(
            self.url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + self.sender_key,
                "X-Automation-Key": self.sender_key,
                "User-Agent": "stackchan-grok-relay/0.1",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.webhook_timeout_seconds) as response:
                response.read(MAX_RESPONSE_BYTES)
        except HTTPError as error:
            return _failure(
                "http_error",
                "routine の起動に失敗しました。routine がオフだと 400 になります。",
                500 <= error.code < 600,
                1,
                detail=f"HTTP {error.code} " + _snippet(_read_error_body(error)),
            )
        except (TimeoutError, socket.timeout):
            return _failure("timeout", "routine の起動が時間内に応答しませんでした。", True, 1)
        except URLError:
            return _failure("network_error", "routine の Webhook に接続できませんでした。", True, 1)

        deadline = self.clock() + self.timeout_seconds
        while True:
            try:
                messages = self.inbox.fetch(reply_to=reply_to)
            except Exception as error:  # noqa: BLE001 - 受信箱の一時障害は待ち続ける
                messages = []
                last_error = str(error)
            else:
                last_error = ""
            for message in messages:
                if getattr(message, "reply_to", None) == reply_to:
                    try:
                        self.inbox.ack(message.id)
                    except Exception:  # noqa: BLE001 - ack 失敗は巡回側が片付ける
                        pass
                    return GrokResult(reply=message.text)
            if self.clock() >= deadline:
                return _failure(
                    "routine_timeout",
                    "routine からの返事が時間内に受信箱へ届きませんでした。",
                    True,
                    1,
                    detail=f"reply_to={reply_to} waited={self.timeout_seconds:.0f}s " + _snippet(last_error),
                )
            self.sleep(self.poll_interval_seconds)


DEFAULT_XAI_URL = "https://api.x.ai/v1/responses"
DEFAULT_XAI_MODEL = "grok-4.3"


@dataclass(frozen=True)
class GrokApiClient:
    """xAI の API（Responses）を直接呼び、同期で一文を受け取ります。

    Webhook routine は非同期（起動確認だけ返す）なので、会話の返答にはこちらを使います。
    鍵はリレーの .env にだけ置きます。
    """

    api_key: str
    model: str = DEFAULT_XAI_MODEL
    system_prompt: str = ""
    url: str = DEFAULT_XAI_URL
    timeout_seconds: float = 20.0
    max_output_tokens: int = 300
    # サーバー側ツール。web_search を許すと天気やニュースを自分で調べて答える（1 回 0.005 ドル）。
    tools: tuple[str, ...] = ()
    location_hint: str = ""  # 「東京」など。天気の質問で場所を聞き返さないために渡す
    timezone_offset_hours: int = 9

    def build_system_prompt(self, now: datetime | None = None) -> str:
        """prompt.txt の文面に、現在日時と所在地、検索の使い方を前置きします。"""
        zone = timezone(timedelta(hours=self.timezone_offset_hours))
        current = (now or datetime.now(zone)).astimezone(zone)
        lines = [f"現在の日時は {current.strftime('%Y-%m-%d %H:%M')}（UTC{self.timezone_offset_hours:+d}）です。"]
        if self.location_hint:
            lines.append(f"話し相手の所在地は「{self.location_hint}」です。場所を聞き返さずこの土地の情報で答えてください。")
        if self.tools:
            lines.append(
                "天気、ニュース、時刻に依存する質問など最新情報が要るときは検索ツールを使い、"
                "その結果を一文にまとめて答えてください。「調べてください」「確認してください」とは答えないでください。"
            )
        else:
            lines.append("最新情報が無くて答えられないときは、その旨を一文で伝えてください。")
        if self.system_prompt:
            lines.append(self.system_prompt)
        return "\n".join(lines)

    def reply(self, utterance: Utterance) -> GrokResult:
        messages = [
            {"role": "system", "content": self.build_system_prompt()},
            {"role": "user", "content": utterance.text},
        ]
        body_fields: dict = {
            "model": self.model,
            "input": messages,
            "store": False,
            "max_output_tokens": self.max_output_tokens,
        }
        if self.tools:
            body_fields["tools"] = [{"type": tool} for tool in self.tools]
        payload = json.dumps(body_fields, ensure_ascii=False).encode("utf-8")
        request = Request(
            self.url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + self.api_key,
                "User-Agent": "stackchan-grok-relay/0.1",
            },
            method="POST",
        )
        for attempt in range(1, 3):
            try:
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    body = response.read(MAX_RESPONSE_BYTES + 1)
                if len(body) > MAX_RESPONSE_BYTES:
                    return _failure("response_too_large", "API の応答が大きすぎます。", False, attempt)
                raw = body.decode("utf-8")
                text = extract_api_text(raw)
                if text is None:
                    return _failure(
                        "invalid_response", "API の応答形式が不正です。", False, attempt, detail=_snippet(raw)
                    )
                return GrokResult(reply=text)
            except HTTPError as error:
                retryable = error.code == 429 or 500 <= error.code < 600
                if retryable and attempt == 1:
                    continue
                return _failure(
                    "http_error",
                    "API が HTTP エラーを返しました。",
                    retryable,
                    attempt,
                    detail=f"HTTP {error.code} " + _snippet(_read_error_body(error)),
                )
            except (TimeoutError, socket.timeout):
                if attempt == 1:
                    continue
                return _failure("timeout", "API が時間内に応答しませんでした。", True, attempt)
            except URLError:
                if attempt == 1:
                    continue
                return _failure("network_error", "API に接続できませんでした。", True, attempt)
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as error:
                return _failure("invalid_response", "API の応答形式が不正です。", False, attempt, detail=str(error))
        return _failure("unknown_error", "API の呼び出しに失敗しました。", False, 2)


def extract_api_text(text: str) -> str | None:
    """Responses API（output[].content[].text）と Chat Completions（choices[0].message.content）の両方を読みます。"""
    decoded = json.loads(text)
    if not isinstance(decoded, dict):
        return None
    if isinstance(decoded.get("output_text"), str):
        return decoded["output_text"]
    output = decoded.get("output")
    if isinstance(output, list):
        pieces: list[str] = []
        for item in output:
            if not isinstance(item, dict) or item.get("type") not in (None, "message"):
                continue
            content = item.get("content")
            if isinstance(content, str):
                pieces.append(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        pieces.append(part["text"])
        if pieces:
            return "".join(pieces)
    choices = decoded.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            return message["content"]
    return None


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
