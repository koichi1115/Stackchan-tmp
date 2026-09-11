import base64
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import sys
from typing import Protocol

from .config import Config
from .device import DEVICE_PATH, SPOKEN_TEXT_HEADER, DeviceAudioService
from .domain import SpokenReply, Utterance, clamp_reply, parse_utterance
from .grok import GrokResult, GrokWebhookClient
from .inbox import InboxClient, InboxPoller
from .session import STREAM_PATH, DeviceSession, SessionRegistry
from .speech import SpeechToText, TextToSpeech, build_speech_to_text, build_text_to_speech
from .vad import SpeechSegmenter
from .wake import WakeWordMatcher
from .websocket import WebSocket, accept_key


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


@dataclass(frozen=True)
class StreamService:
    """WebSocket で常時つながるロボット向けの設定一式。接続ごとに DeviceSession を作ります。"""

    relay: RelayService
    speech_to_text: SpeechToText
    text_to_speech: TextToSpeech
    wake_matcher: WakeWordMatcher
    wake_ack_text: str
    wake_window_seconds: float
    vad_threshold: float
    vad_silence_ms: float
    vad_max_seconds: float
    registry: SessionRegistry

    def new_session(self, socket: WebSocket) -> DeviceSession:
        return DeviceSession(
            socket=socket,
            relay=self.relay,
            speech_to_text=self.speech_to_text,
            text_to_speech=self.text_to_speech,
            wake_matcher=self.wake_matcher,
            wake_ack_text=self.wake_ack_text,
            wake_window_seconds=self.wake_window_seconds,
            segmenter=SpeechSegmenter(
                threshold=self.vad_threshold,
                silence_ms=self.vad_silence_ms,
                max_seconds=self.vad_max_seconds,
            ),
        )


def build_server(config: Config) -> ThreadingHTTPServer:
    client = GrokWebhookClient(
        url=config.webhook_url,
        sender_key=config.webhook_sender_key,
        timeout_seconds=config.timeout_seconds,
    )
    service = RelayService(client=client, max_reply_length=config.max_reply_length)
    speech_to_text = build_speech_to_text(
        engine=config.stt_engine,
        url=config.stt_url,
        timeout_seconds=config.speech_timeout_seconds,
        mock_transcript=config.mock_transcript,
        prompt=config.stt_prompt,
    )
    text_to_speech = build_text_to_speech(
        engine=config.tts_engine,
        url=config.tts_url,
        speaker_id=config.tts_speaker_id,
        timeout_seconds=config.speech_timeout_seconds,
    )
    device_service = DeviceAudioService(
        relay=service,
        speech_to_text=speech_to_text,
        text_to_speech=text_to_speech,
        max_audio_bytes=config.max_audio_bytes,
    )
    registry = SessionRegistry()
    stream_service = StreamService(
        relay=service,
        speech_to_text=speech_to_text,
        text_to_speech=text_to_speech,
        wake_matcher=WakeWordMatcher(words=config.wake_words),
        wake_ack_text=config.wake_ack_text,
        wake_window_seconds=config.wake_window_seconds,
        vad_threshold=config.vad_threshold,
        vad_silence_ms=config.vad_silence_ms,
        vad_max_seconds=config.vad_max_seconds,
        registry=registry,
    )
    server = ThreadingHTTPServer(
        (config.host, config.port), _handler_for(service, device_service, stream_service)
    )
    if config.inbox_url:
        poller = InboxPoller(
            client=InboxClient(url=config.inbox_url, poll_key=config.inbox_poll_key),
            registry=registry,
            text_to_speech=text_to_speech,
            interval_seconds=config.inbox_poll_seconds,
            ttl_seconds=config.inbox_message_ttl_seconds,
        )
        poller.start()
        server.inbox_poller = poller  # type: ignore[attr-defined]
    return server


def _handler_for(
    service: RelayService,
    device_service: DeviceAudioService | None = None,
    stream_service: StreamService | None = None,
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == STREAM_PATH and stream_service is not None:
                self._handle_stream(stream_service)
                return
            self._send(
                405,
                {"error": {"code": "method_not_allowed", "message": "POST を使用してください。"}},
            )

        def _handle_stream(self, streams: StreamService) -> None:
            key = self.headers.get("Sec-WebSocket-Key", "")
            upgrade = self.headers.get("Upgrade", "").lower()
            if upgrade != "websocket" or not key:
                self._send(
                    426,
                    {"error": {"code": "upgrade_required", "message": "WebSocket で接続してください。"}},
                )
                return
            self.close_connection = True
            self.wfile.write(
                (
                    "HTTP/1.1 101 Switching Protocols\r\n"
                    "Upgrade: websocket\r\n"
                    "Connection: Upgrade\r\n"
                    f"Sec-WebSocket-Accept: {accept_key(key)}\r\n\r\n"
                ).encode("ascii")
            )
            self.wfile.flush()
            socket = WebSocket(reader=self.rfile, writer=self.wfile, is_client=False)
            session = streams.new_session(socket)
            streams.registry.add(session)
            try:
                session.run()
            finally:
                streams.registry.remove(session)
                socket.close()

        def do_POST(self) -> None:
            if self.path == DEVICE_PATH and device_service is not None:
                self._handle_device(device_service)
                return
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

        def _handle_device(self, device: DeviceAudioService) -> None:
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > device.max_audio_bytes:
                    raise ValueError("録音の大きさが不正です。")
                audio = self.rfile.read(length)
            except ValueError as error:
                self._send(400, {"error": {"code": "invalid_request", "message": str(error)}})
                return

            reply = device.handle(audio)
            if reply.is_silent:
                self._send_silence()
                return

            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(reply.audio)))
            self.send_header(
                SPOKEN_TEXT_HEADER,
                base64.b64encode(reply.text.encode("utf-8")).decode("ascii"),
            )
            self.end_headers()
            self.wfile.write(reply.audio)

        def _send_silence(self) -> None:
            self.send_response(204)
            self.end_headers()

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
