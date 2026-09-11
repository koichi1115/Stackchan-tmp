"""ロボット一台ぶんの WebSocket セッション。

ロボットから届く PCM を VAD で区切り、文字起こし → ウェイクワード判定 → Grok → 合成 → 送出、
の順に処理します。ウェイクワードを含まない発話は捨て、どこにも送りません。
"""

from dataclasses import dataclass, field
import json
import queue
import sys
import threading
import time
from typing import Callable, Protocol

from .domain import SpokenReply
from .speech import SpeechError, SpeechToText, TextToSpeech
from .vad import SpeechSegmenter
from .wake import WakeWordMatcher, clean_transcript
from .wav import DEVICE_CHANNELS, DEVICE_SAMPLE_RATE, DEVICE_SAMPLE_WIDTH, WavAudio, encode_wav
from .websocket import OP_BINARY, OP_TEXT, ConnectionClosed, WebSocket, WebSocketError

STREAM_PATH = "/device/stream"
WAV_CHUNK_BYTES = 8_192
MAX_SPEAK_BYTES = 2 * 1024 * 1024
PLAYED_TIMEOUT_SECONDS = 60.0

STATE_LISTENING = "listening"
STATE_AWAKE = "awake"
STATE_THINKING = "thinking"
STATE_SPEAKING = "speaking"

BUTTON_EVENT = b"button"


class SpeakHandler(Protocol):
    def handle(self, body: object) -> SpokenReply:
        ...


@dataclass
class SessionRegistry:
    """接続中のロボット一覧。受信箱の読み上げ先を探すのに使います。"""

    _sessions: list["DeviceSession"] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, session: "DeviceSession") -> None:
        with self._lock:
            self._sessions.append(session)

    def remove(self, session: "DeviceSession") -> None:
        with self._lock:
            if session in self._sessions:
                self._sessions.remove(session)

    def sessions(self) -> list["DeviceSession"]:
        with self._lock:
            return list(self._sessions)


@dataclass
class DeviceSession:
    socket: WebSocket
    relay: SpeakHandler
    speech_to_text: SpeechToText
    text_to_speech: TextToSpeech
    wake_matcher: WakeWordMatcher
    wake_ack_text: str = "はい？"
    wake_window_seconds: float = 8.0
    segmenter: SpeechSegmenter = field(default_factory=SpeechSegmenter)
    device_name: str = "unknown"
    clock: Callable[[], float] = time.monotonic
    _state: str = STATE_LISTENING
    _awake_until: float = 0.0
    _segments: "queue.Queue[bytes]" = field(default_factory=lambda: queue.Queue(maxsize=1))
    _played: threading.Event = field(default_factory=threading.Event)
    _speech_lock: threading.Lock = field(default_factory=threading.Lock)
    _state_lock: threading.Lock = field(default_factory=threading.Lock)
    _closed: threading.Event = field(default_factory=threading.Event)

    @property
    def state(self) -> str:
        return self._state

    @property
    def is_open(self) -> bool:
        return not self._closed.is_set()

    def run(self) -> None:
        """接続が切れるまで受信し続けます。呼び出したスレッドを占有します。"""
        worker = threading.Thread(target=self._process_segments, daemon=True)
        worker.start()
        try:
            self._send_state(STATE_LISTENING)
            while True:
                opcode, payload = self.socket.receive()
                if opcode == OP_BINARY:
                    self._on_audio(payload)
                elif opcode == OP_TEXT:
                    self._on_text(payload)
        except (ConnectionClosed, WebSocketError, OSError) as error:
            _log("device_disconnected", str(error), device=self.device_name)
        finally:
            self._closed.set()
            self._played.set()
            try:
                self._segments.put_nowait(b"")
            except queue.Full:
                pass
            worker.join(timeout=2)

    def speak(self, text: str, audio: bytes) -> bool:
        """WAV を送り、鳴らし終わるまで待ちます。順番待ちはこの中で行います。"""
        if not audio or len(audio) > MAX_SPEAK_BYTES or not self.is_open:
            return False
        with self._speech_lock:
            if not self.is_open:
                return False
            previous = self._state
            self._played.clear()
            self._set_state(STATE_SPEAKING)
            try:
                self.socket.send_text(
                    json.dumps({"type": "speak", "text": text, "bytes": len(audio)}, ensure_ascii=False)
                )
                for offset in range(0, len(audio), WAV_CHUNK_BYTES):
                    self.socket.send_binary(audio[offset : offset + WAV_CHUNK_BYTES])
            except OSError as error:
                _log("device_send_failed", str(error), device=self.device_name)
                return False
            finished = self._played.wait(PLAYED_TIMEOUT_SECONDS)
            if not finished:
                _log("played_timeout", "ロボットから played が返りませんでした。", device=self.device_name)
            self.segmenter.reset()
            if previous == STATE_AWAKE and self.clock() < self._awake_until:
                self._send_state(STATE_AWAKE)
            else:
                self._send_state(STATE_LISTENING)
            return finished and self.is_open

    # --- 受信側 ---

    def _on_audio(self, pcm: bytes) -> None:
        with self._state_lock:
            state = self._state
            if state == STATE_AWAKE and self.clock() >= self._awake_until:
                self._state = STATE_LISTENING
                state = STATE_LISTENING
                self._send_state_unlocked(STATE_LISTENING)
        if state not in (STATE_LISTENING, STATE_AWAKE):
            return
        segment = self.segmenter.feed(pcm)
        if segment is None:
            return
        try:
            self._segments.put_nowait(segment)
        except queue.Full:
            _log("segment_dropped", "前の発話を処理中のため捨てました。", device=self.device_name)

    def _on_text(self, payload: bytes) -> None:
        try:
            message = json.loads(payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            _log("device_bad_message", "JSON として読めないメッセージを受け取りました。", device=self.device_name)
            return
        if not isinstance(message, dict):
            return
        kind = message.get("type")
        if kind == "hello":
            device = message.get("device")
            if isinstance(device, str) and device:
                self.device_name = device[:32]
            _log("device_hello", "ロボットが接続しました。", device=self.device_name)
        elif kind == "played":
            self._played.set()
        elif kind == "button":
            self._on_button()

    def _on_button(self) -> None:
        """ボタン A はウェイクワードの代わりです。返事はワーカー側で行います（受信スレッドを塞がない）。"""
        if self._state in (STATE_LISTENING, STATE_AWAKE):
            self.segmenter.reset()
            try:
                self._segments.put_nowait(BUTTON_EVENT)
            except queue.Full:
                pass

    # --- 処理側 ---

    def _process_segments(self) -> None:
        while self.is_open:
            segment = self._segments.get()
            if not segment or not self.is_open:
                continue
            if segment == BUTTON_EVENT:
                self._acknowledge_wake()
                continue
            self._handle_segment(segment)

    def _handle_segment(self, pcm: bytes) -> None:
        was_awake = self._state == STATE_AWAKE and self.clock() < self._awake_until
        self._set_state(STATE_THINKING)
        try:
            transcript = self._transcribe(pcm)
            if not transcript:
                self._return_to_listening(was_awake)
                return

            if was_awake:
                text = transcript
            else:
                match = self.wake_matcher.match(transcript)
                if not match.matched:
                    # ウェイクワードが無い発話は捨てます。本文はログにも残しません。
                    _log("ignored_speech", "呼びかけが無いため捨てました。", device=self.device_name)
                    self._return_to_listening(False)
                    return
                if not match.remainder:
                    self._acknowledge_wake()
                    return
                text = match.remainder

            self._converse(text)
        except Exception as error:  # noqa: BLE001 - 一件の失敗で接続を落とさない
            _log("session_error", f"{type(error).__name__}: {error}", device=self.device_name)
            self._return_to_listening(False)

    def _transcribe(self, pcm: bytes) -> str:
        audio = encode_wav(
            WavAudio(
                sample_rate=DEVICE_SAMPLE_RATE,
                channels=DEVICE_CHANNELS,
                sample_width=DEVICE_SAMPLE_WIDTH,
                frames=pcm,
            )
        )
        try:
            return clean_transcript(self.speech_to_text.transcribe(audio))
        except SpeechError as error:
            _log(error.code, error.message, device=self.device_name)
            return ""

    def _converse(self, text: str) -> None:
        try:
            spoken = self.relay.handle({"text": text})
        except ValueError as error:
            _log("invalid_transcript", str(error), device=self.device_name)
            self._return_to_listening(False)
            return
        if not spoken.speak:
            self._return_to_listening(False)
            return
        audio = self._synthesize(spoken.speak)
        if audio is None:
            self._return_to_listening(False)
            return
        self._awake_until = 0.0
        self.speak(spoken.speak, audio)

    def _acknowledge_wake(self) -> None:
        audio = self._synthesize(self.wake_ack_text)
        self._awake_until = self.clock() + self.wake_window_seconds
        if audio is None:
            self._send_state(STATE_AWAKE)
            return
        self._set_state(STATE_AWAKE)
        self.speak(self.wake_ack_text, audio)
        # speak() は待ち受け窓が残っていれば awake に戻します。

    def _synthesize(self, text: str) -> bytes | None:
        try:
            audio = self.text_to_speech.synthesize(text)
        except SpeechError as error:
            _log(error.code, error.message, device=self.device_name)
            return None
        if not audio:
            _log("empty_synthesis", "音声合成の結果が空でした。", device=self.device_name)
            return None
        return audio

    def _return_to_listening(self, keep_awake: bool) -> None:
        self.segmenter.reset()
        if keep_awake and self.clock() < self._awake_until:
            self._send_state(STATE_AWAKE)
        else:
            self._send_state(STATE_LISTENING)

    # --- 状態 ---

    def _set_state(self, state: str) -> None:
        with self._state_lock:
            self._state = state

    def _send_state(self, state: str) -> None:
        with self._state_lock:
            self._state = state
            self._send_state_unlocked(state)

    def _send_state_unlocked(self, state: str) -> None:
        if not self.is_open:
            return
        try:
            self.socket.send_text(json.dumps({"type": "state", "state": state}))
        except OSError:
            pass


def _log(code: str, message: str, device: str) -> None:
    print(
        json.dumps(
            {"event": "device_session", "code": code, "message": message, "device": device},
            ensure_ascii=False,
        ),
        file=sys.stderr,
        flush=True,
    )
