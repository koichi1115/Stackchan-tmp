"""音量で発話を区切る簡単な VAD。純 Python で外部依存はありません。

16 kHz・モノラル・16 bit PCM を 1024 サンプルずつ受け取り、
「発話が終わった」時点でその区間の PCM を返します。
"""

from dataclasses import dataclass, field
import math
import struct

FRAME_SAMPLES = 1024
SAMPLE_RATE = 16_000
FRAME_MS = FRAME_SAMPLES * 1000 / SAMPLE_RATE  # 64 ms


def frame_rms(pcm: bytes) -> float:
    count = len(pcm) // 2
    if count == 0:
        return 0.0
    samples = struct.unpack(f"<{count}h", pcm[: count * 2])
    return math.sqrt(sum(sample * sample for sample in samples) / count)


@dataclass
class SpeechSegmenter:
    threshold: float = 600.0
    silence_ms: float = 700.0
    max_seconds: float = 12.0
    start_ms: float = 100.0
    min_speech_ms: float = 300.0
    preroll_frames: int = 5
    noise_multiplier: float = 2.5
    _noise_floor: float = 0.0
    _loud_run: int = 0
    _quiet_run: int = 0
    _in_speech: bool = False
    _speech_frames: int = 0
    _preroll: list[bytes] = field(default_factory=list)
    _buffer: bytearray = field(default_factory=bytearray)

    @property
    def in_speech(self) -> bool:
        return self._in_speech

    def reset(self) -> None:
        self._loud_run = 0
        self._quiet_run = 0
        self._in_speech = False
        self._speech_frames = 0
        self._preroll.clear()
        self._buffer = bytearray()

    def feed(self, pcm: bytes) -> bytes | None:
        """フレームを一つ与え、発話が終わったらその PCM を返します。"""
        rms = frame_rms(pcm)
        effective = self.effective_threshold
        loud = rms >= effective
        self._update_noise(rms)

        if not self._in_speech:
            self._preroll.append(pcm)
            if len(self._preroll) > self.preroll_frames:
                self._preroll.pop(0)
            if loud:
                self._loud_run += 1
                if self._loud_run * FRAME_MS >= self.start_ms:
                    self._in_speech = True
                    self._buffer = bytearray(b"".join(self._preroll))
                    self._speech_frames = len(self._preroll)
                    self._quiet_run = 0
                    self._preroll.clear()
            else:
                self._loud_run = 0
            return None

        self._buffer += pcm
        self._speech_frames += 1
        if loud:
            self._quiet_run = 0
        else:
            self._quiet_run += 1

        ended = self._quiet_run * FRAME_MS >= self.silence_ms
        too_long = self._speech_frames * FRAME_MS >= self.max_seconds * 1000
        if not (ended or too_long):
            return None

        segment = bytes(self._buffer)
        voiced_frames = self._speech_frames - self._quiet_run
        self.reset()
        if voiced_frames * FRAME_MS < self.min_speech_ms:
            return None
        return segment

    @property
    def effective_threshold(self) -> float:
        return max(self.threshold, self._noise_floor * self.noise_multiplier)

    def _update_noise(self, rms: float) -> None:
        """雑音床は下がるときは速く、上がるときはゆっくり追従します。発話の山には引きずられません。"""
        if rms < self._noise_floor:
            self._noise_floor = self._noise_floor * 0.8 + rms * 0.2
        else:
            # 上昇は時定数およそ 30 秒。一言の発話（数秒）では床はほとんど動きません。
            self._noise_floor = self._noise_floor * 0.998 + rms * 0.002
