import contextlib
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import threading
import unittest

from stackchan_grok_relay.inbox import InboxClient, InboxError, InboxPoller
from stackchan_grok_relay.session import SessionRegistry
from stackchan_grok_relay.speech import MockTextToSpeech


class FakeInbox:
    """Worker 受信箱の振る舞いをまねる HTTP サーバーです。"""

    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.acked: list[int] = []
        self.poll_key = "poll-key"

    def handler(self) -> type[BaseHTTPRequestHandler]:
        inbox = self

        class Handler(BaseHTTPRequestHandler):
            def _authorized(self) -> bool:
                return self.headers.get("Authorization") == f"Bearer {inbox.poll_key}"

            def do_GET(self) -> None:
                if not self._authorized():
                    self._json(401, {"error": "unauthorized"})
                    return
                pending = [m for m in inbox.messages if m["id"] not in inbox.acked]
                self._json(200, {"messages": pending})

            def do_POST(self) -> None:
                if not self._authorized():
                    self._json(401, {"error": "unauthorized"})
                    return
                parts = self.path.strip("/").split("/")
                if len(parts) == 3 and parts[0] == "messages" and parts[2] == "ack":
                    inbox.acked.append(int(parts[1]))
                    self._json(200, {"id": int(parts[1]), "acked": True})
                    return
                self._json(404, {"error": "not_found"})

            def _json(self, status: int, body: object) -> None:
                payload = json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format: str, *args: object) -> None:
                return

        return Handler


@contextlib.contextmanager
def running_inbox():
    inbox = FakeInbox()
    server = ThreadingHTTPServer(("127.0.0.1", 0), inbox.handler())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield inbox, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class FakeSession:
    def __init__(self, succeed: bool = True) -> None:
        self.spoken: list[str] = []
        self.succeed = succeed
        self.is_open = True

    def speak(self, text: str, audio: bytes) -> bool:
        self.spoken.append(text)
        return self.succeed


def now_iso(delta_seconds: float = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_seconds)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class InboxClientTests(unittest.TestCase):
    def test_fetch_and_ack(self) -> None:
        with running_inbox() as (inbox, url):
            inbox.messages.append({"id": 1, "text": "ただいま", "created_at": now_iso()})
            client = InboxClient(url=url, poll_key="poll-key")
            messages = client.fetch()
            self.assertEqual([(m.id, m.text) for m in messages], [(1, "ただいま")])
            self.assertIsNotNone(messages[0].created_at)
            client.ack(1)
            self.assertEqual(inbox.acked, [1])
            self.assertEqual(client.fetch(), [])

    def test_wrong_key_is_an_error(self) -> None:
        with running_inbox() as (_, url):
            client = InboxClient(url=url, poll_key="wrong")
            with self.assertRaises(InboxError):
                client.fetch()


class InboxPollerTests(unittest.TestCase):
    def _poller(self, url: str, registry: SessionRegistry, ttl: float = 3600) -> InboxPoller:
        return InboxPoller(
            client=InboxClient(url=url, poll_key="poll-key"),
            registry=registry,
            text_to_speech=MockTextToSpeech(),
            interval_seconds=1,
            ttl_seconds=ttl,
        )

    def test_speaks_and_acks_when_a_robot_is_connected(self) -> None:
        with running_inbox() as (inbox, url):
            inbox.messages.append({"id": 1, "text": "  おかえり\n今日は早いね ", "created_at": now_iso()})
            registry = SessionRegistry()
            session = FakeSession()
            registry.add(session)  # type: ignore[arg-type]
            with contextlib.redirect_stderr(io.StringIO()):
                delivered = self._poller(url, registry).poll_once()
        self.assertEqual(delivered, 1)
        self.assertEqual(session.spoken, ["おかえり 今日は早いね"])
        self.assertEqual(inbox.acked, [1])

    def test_does_not_fetch_without_a_robot(self) -> None:
        with running_inbox() as (inbox, url):
            inbox.messages.append({"id": 1, "text": "おかえり", "created_at": now_iso()})
            delivered = self._poller(url, SessionRegistry()).poll_once()
        self.assertEqual(delivered, 0)
        self.assertEqual(inbox.acked, [])

    def test_failed_playback_keeps_the_message(self) -> None:
        with running_inbox() as (inbox, url):
            inbox.messages.append({"id": 1, "text": "おかえり", "created_at": now_iso()})
            registry = SessionRegistry()
            registry.add(FakeSession(succeed=False))  # type: ignore[arg-type]
            delivered = self._poller(url, registry).poll_once()
        self.assertEqual(delivered, 0)
        self.assertEqual(inbox.acked, [])

    def test_expired_message_is_acked_silently(self) -> None:
        with running_inbox() as (inbox, url):
            inbox.messages.append({"id": 7, "text": "古い", "created_at": now_iso(-7200)})
            registry = SessionRegistry()
            session = FakeSession()
            registry.add(session)  # type: ignore[arg-type]
            with contextlib.redirect_stderr(io.StringIO()):
                delivered = self._poller(url, registry, ttl=3600).poll_once()
        self.assertEqual(delivered, 0)
        self.assertEqual(session.spoken, [])
        self.assertEqual(inbox.acked, [7])
