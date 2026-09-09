"""実機（非公式ファームウェア）向けの音声一往復。

ロボットは録音を送り、発話用の音声を受け取るだけです。認識・返答・合成はすべてリレー側で行います。
空応答とエラーは無音（音声なし）にします。エラーを発話することはありません。
"""

from dataclasses import dataclass
import json
import sys
from typing import Protocol

from .domain import SpokenReply
from .speech import SpeechError, SpeechToText, TextToSpeech


DEVICE_PATH = "/device/utterance"
SPOKEN_TEXT_HEADER = "X-Spoken-Text-Base64"


class SpeakHandler(Protocol):
    def handle(self, body: object) -> SpokenReply:
        ...


@dataclass(frozen=True)
class DeviceReply:
    text: str
    audio: bytes

    @property
    def is_silent(self) -> bool:
        return not self.audio


SILENCE = DeviceReply(text="", audio=b"")


@dataclass(frozen=True)
class DeviceAudioService:
    relay: SpeakHandler
    speech_to_text: SpeechToText
    text_to_speech: TextToSpeech
    max_audio_bytes: int

    def handle(self, audio: bytes) -> DeviceReply:
        if not audio or len(audio) > self.max_audio_bytes:
            _log("device_audio_rejected", "録音の大きさが不正です。")
            return SILENCE

        try:
            transcript = self.speech_to_text.transcribe(audio).strip()
        except SpeechError as error:
            _log(error.code, error.message)
            return SILENCE

        if not transcript:
            _log("empty_transcript", "録音から発話を検出できませんでした。")
            return SILENCE

        try:
            spoken = self.relay.handle({"text": transcript})
        except ValueError as error:
            _log("invalid_transcript", str(error))
            return SILENCE

        if not spoken.speak:
            return SILENCE

        try:
            reply_audio = self.text_to_speech.synthesize(spoken.speak)
        except SpeechError as error:
            _log(error.code, error.message)
            return SILENCE

        if not reply_audio:
            _log("empty_synthesis", "音声合成の結果が空でした。")
            return SILENCE

        return DeviceReply(text=spoken.speak, audio=reply_audio)


def _log(code: str, message: str) -> None:
    print(
        json.dumps(
            {"event": "device_audio_error", "code": code, "message": message},
            ensure_ascii=False,
        ),
        file=sys.stderr,
    )
