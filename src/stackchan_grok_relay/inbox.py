"""Cloudflare Worker の受信箱を巡回し、届いたテキストをロボットに読み上げさせます。

巡回は Mac からの外向き HTTPS だけです。家のネットワークに受信口は作りません。
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import socket
import sys
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .domain import clean_text
from .session import SessionRegistry
from .speech import SpeechError, TextToSpeech

MAX_RESPONSE_BYTES = 65_536


class InboxError(RuntimeError):
    pass


@dataclass(frozen=True)
class InboxMessage:
    id: int
    text: str
    created_at: datetime | None


@dataclass(frozen=True)
class InboxClient:
    url: str
    poll_key: str
    timeout_seconds: float = 10.0

    def fetch(self, after: int = 0, limit: int = 20) -> list[InboxMessage]:
        query = urlencode({"after": after, "limit": limit})
        request = Request(f"{self._base}/messages?{query}", headers=self._headers(), method="GET")
        body = self._call(request)
        raw = body.get("messages")
        if not isinstance(raw, list):
            raise InboxError("受信箱の応答形式が不正です。")
        messages: list[InboxMessage] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            identifier = item.get("id")
            text = item.get("text")
            if not isinstance(identifier, int) or not isinstance(text, str):
                continue
            messages.append(
                InboxMessage(id=identifier, text=text, created_at=_parse_time(item.get("created_at")))
            )
        return messages

    def ack(self, message_id: int) -> None:
        request = Request(
            f"{self._base}/messages/{int(message_id)}/ack",
            data=b"",
            headers=self._headers(),
            method="POST",
        )
        self._call(request)

    @property
    def _base(self) -> str:
        return self.url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.poll_key}", "Accept": "application/json"}

    def _call(self, request: Request) -> dict:
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as error:
            raise InboxError(f"受信箱が HTTP {error.code} を返しました。") from error
        except (TimeoutError, socket.timeout) as error:
            raise InboxError("受信箱が時間内に応答しませんでした。") from error
        except URLError as error:
            raise InboxError("受信箱に接続できませんでした。") from error
        if len(body) > MAX_RESPONSE_BYTES:
            raise InboxError("受信箱の応答が大きすぎます。")
        try:
            decoded = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise InboxError("受信箱の応答が JSON ではありません。") from error
        if not isinstance(decoded, dict):
            raise InboxError("受信箱の応答形式が不正です。")
        return decoded


@dataclass
class InboxPoller:
    client: InboxClient
    registry: SessionRegistry
    text_to_speech: TextToSpeech
    interval_seconds: float = 5.0
    ttl_seconds: float = 6 * 60 * 60
    max_length: int = 500
    _stop: threading.Event = field(default_factory=threading.Event)
    _spoken_ids: set[int] = field(default_factory=set)

    def start(self) -> threading.Thread:
        thread = threading.Thread(target=self.run, name="inbox-poller", daemon=True)
        thread.start()
        return thread

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except InboxError as error:
                _log("inbox_error", str(error))
            except Exception as error:  # noqa: BLE001 - 巡回は止めない
                _log("inbox_unexpected", f"{type(error).__name__}: {error}")
            self._stop.wait(self.interval_seconds)

    def poll_once(self, now: datetime | None = None) -> int:
        """一回巡回し、読み上げた件数を返します。ロボットが未接続なら取り出しません。"""
        sessions = [session for session in self.registry.sessions() if session.is_open]
        if not sessions:
            return 0
        delivered = 0
        for message in self.client.fetch():
            if message.id in self._spoken_ids:
                self._try_ack(message.id)
                continue
            if self._expired(message, now):
                _log("inbox_expired", f"古いメッセージ {message.id} を読み上げずに片付けました。")
                self._try_ack(message.id)
                continue
            text = clean_text(message.text, self.max_length)
            if not text:
                self._try_ack(message.id)
                continue
            try:
                audio = self.text_to_speech.synthesize(text)
            except SpeechError as error:
                _log(error.code, error.message)
                return delivered
            spoken = False
            for session in sessions:
                if session.speak(text, audio):
                    spoken = True
            if not spoken:
                return delivered
            self._spoken_ids.add(message.id)
            self._try_ack(message.id)
            delivered += 1
        return delivered

    def _try_ack(self, message_id: int) -> None:
        try:
            self.client.ack(message_id)
        except InboxError as error:
            _log("inbox_ack_failed", f"{message_id}: {error}")
            return
        self._spoken_ids.discard(message_id)

    def _expired(self, message: InboxMessage, now: datetime | None) -> bool:
        if message.created_at is None:
            return False
        current = now or datetime.now(timezone.utc)
        return (current - message.created_at).total_seconds() > self.ttl_seconds


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _log(code: str, message: str) -> None:
    print(
        json.dumps({"event": "inbox", "code": code, "message": message}, ensure_ascii=False),
        file=sys.stderr,
        flush=True,
    )


def sleep_seconds(seconds: float) -> None:
    time.sleep(seconds)
