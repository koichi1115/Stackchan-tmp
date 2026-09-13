"""音声認識と音声合成の差し替え可能な実装。

鍵や接続先はリレーの `.env` からだけ読み込みます。ロボット側には一切置きません。
"""

from dataclasses import dataclass
import json
import socket
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .wav import WavError, decode_wav, tone_wav


MAX_SPEECH_RESPONSE_BYTES = 4_000_000
MOCK_ENGINE = "mock"
WHISPER_HTTP_ENGINE = "whisper_http"
VOICEVOX_HTTP_ENGINE = "voicevox_http"
STT_ENGINES = (MOCK_ENGINE, WHISPER_HTTP_ENGINE)
TTS_ENGINES = (MOCK_ENGINE, VOICEVOX_HTTP_ENGINE)


class SpeechError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class SpeechToText(Protocol):
    def transcribe(self, audio: bytes) -> str:
        """録音一件を書き起こします。何も話していない場合は空文字を返します。"""


class TextToSpeech(Protocol):
    def synthesize(self, text: str) -> bytes:
        """一文を WAV バイト列にします。"""


@dataclass(frozen=True)
class MockSpeechToText:
    transcript: str

    def transcribe(self, audio: bytes) -> str:
        try:
            decoded = decode_wav(audio)
        except WavError as error:
            raise SpeechError("invalid_audio", "録音を WAV として読み取れません。") from error
        if decoded.frame_count == 0:
            return ""
        return self.transcript


@dataclass(frozen=True)
class MockTextToSpeech:
    def synthesize(self, text: str) -> bytes:
        if not text:
            raise SpeechError("empty_text", "合成する文字列がありません。")
        return tone_wav(seconds=min(3.0, 0.1 * len(text)))


@dataclass(frozen=True)
class WhisperHttpSpeechToText:
    """whisper.cpp の HTTP サーバー（`/inference`）に音声を渡します。鍵は不要です。

    `prompt` は whisper の初期プロンプトです。ウェイクワードのような固有名詞は、
    これを渡さないと近い音の一般語に置き換えられます（「スタックちゃん」→「スタークちゃん」）。
    語を並べると語順が崩れたり無音時の幻聴に紛れ込んだりするため、一語だけ渡します。
    """

    url: str
    timeout_seconds: float
    prompt: str = ""

    def transcribe(self, audio: bytes) -> str:
        boundary = "----stackchan-grok-relay-boundary"
        parts = [
            f"--{boundary}\r\n".encode("utf-8"),
            b'Content-Disposition: form-data; name="file"; filename="utterance.wav"\r\n',
            b"Content-Type: audio/wav\r\n\r\n",
            audio,
            f"\r\n--{boundary}\r\n".encode("utf-8"),
            b'Content-Disposition: form-data; name="response_format"\r\n\r\njson\r\n',
        ]
        if self.prompt:
            parts.append(f"--{boundary}\r\n".encode("utf-8"))
            parts.append(b'Content-Disposition: form-data; name="prompt"\r\n\r\n')
            parts.append(self.prompt.encode("utf-8"))
            parts.append(b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode("utf-8"))
        body = b"".join(parts)
        request = Request(
            self.url,
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        decoded = _post_json(request, self.timeout_seconds, "stt")
        text = decoded.get("text")
        if not isinstance(text, str):
            raise SpeechError("stt_invalid_response", "音声認識の応答形式が不正です。")
        return text.strip()


@dataclass(frozen=True)
class VoicevoxHttpTextToSpeech:
    """VOICEVOX ENGINE の HTTP API で一文を合成します。鍵は不要です。"""

    url: str
    speaker_id: int
    timeout_seconds: float
    speed_scale: float = 1.0  # 話速。1.0 が VOICEVOX の既定。1.1〜1.2 で歯切れがよくなる
    volume_scale: float = 1.0  # 音量。ロボットのスピーカーが小さいときに上げる

    def synthesize(self, text: str) -> bytes:
        if not text:
            raise SpeechError("empty_text", "合成する文字列がありません。")

        query_url = f"{self.url.rstrip('/')}/audio_query?" + urlencode(
            {"text": text, "speaker": self.speaker_id}
        )
        query = _post_json(
            Request(query_url, data=b"", method="POST"),
            self.timeout_seconds,
            "tts",
        )
        if self.speed_scale != 1.0:
            query["speedScale"] = self.speed_scale
        if self.volume_scale != 1.0:
            query["volumeScale"] = self.volume_scale

        synthesis_url = f"{self.url.rstrip('/')}/synthesis?" + urlencode(
            {"speaker": self.speaker_id}
        )
        request = Request(
            synthesis_url,
            data=json.dumps(query).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "audio/wav"},
            method="POST",
        )
        audio = _post_bytes(request, self.timeout_seconds, "tts")
        try:
            decode_wav(audio)
        except WavError as error:
            raise SpeechError("tts_invalid_audio", "音声合成の応答が WAV ではありません。") from error
        return audio


def build_speech_to_text(
    engine: str,
    url: str,
    timeout_seconds: float,
    mock_transcript: str,
    prompt: str = "",
) -> SpeechToText:
    if engine == MOCK_ENGINE:
        return MockSpeechToText(transcript=mock_transcript)
    if engine == WHISPER_HTTP_ENGINE:
        return WhisperHttpSpeechToText(
            url=url, timeout_seconds=timeout_seconds, prompt=prompt
        )
    raise ValueError(f"STT_ENGINE は {STT_ENGINES} のいずれかにしてください。")


def build_text_to_speech(
    engine: str,
    url: str,
    speaker_id: int,
    timeout_seconds: float,
    speed_scale: float = 1.0,
    volume_scale: float = 1.0,
) -> TextToSpeech:
    if engine == MOCK_ENGINE:
        return MockTextToSpeech()
    if engine == VOICEVOX_HTTP_ENGINE:
        return VoicevoxHttpTextToSpeech(
            url=url,
            speaker_id=speaker_id,
            timeout_seconds=timeout_seconds,
            speed_scale=speed_scale,
            volume_scale=volume_scale,
        )
    raise ValueError(f"TTS_ENGINE は {TTS_ENGINES} のいずれかにしてください。")


def _post_bytes(request: Request, timeout_seconds: float, stage: str) -> bytes:
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            body = response.read(MAX_SPEECH_RESPONSE_BYTES + 1)
    except HTTPError as error:
        raise SpeechError(f"{stage}_http_error", "音声エンジンが HTTP エラーを返しました。") from error
    except (TimeoutError, socket.timeout) as error:
        raise SpeechError(f"{stage}_timeout", "音声エンジンが時間内に応答しませんでした。") from error
    except URLError as error:
        raise SpeechError(f"{stage}_network_error", "音声エンジンに接続できませんでした。") from error
    if len(body) > MAX_SPEECH_RESPONSE_BYTES:
        raise SpeechError(f"{stage}_response_too_large", "音声エンジンの応答が大きすぎます。")
    return body


def _post_json(request: Request, timeout_seconds: float, stage: str) -> dict:
    body = _post_bytes(request, timeout_seconds, stage)
    try:
        decoded = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise SpeechError(f"{stage}_invalid_response", "音声エンジンの応答形式が不正です。") from error
    if not isinstance(decoded, dict):
        raise SpeechError(f"{stage}_invalid_response", "音声エンジンの応答形式が不正です。")
    return decoded
