from dataclasses import dataclass
import io
import math
import struct
import wave


DEVICE_SAMPLE_RATE = 16_000
DEVICE_CHANNELS = 1
DEVICE_SAMPLE_WIDTH = 2


class WavError(ValueError):
    pass


@dataclass(frozen=True)
class WavAudio:
    sample_rate: int
    channels: int
    sample_width: int
    frames: bytes

    @property
    def frame_count(self) -> int:
        bytes_per_frame = self.channels * self.sample_width
        return len(self.frames) // bytes_per_frame if bytes_per_frame else 0


def decode_wav(data: bytes) -> WavAudio:
    try:
        with wave.open(io.BytesIO(data), "rb") as source:
            return WavAudio(
                sample_rate=source.getframerate(),
                channels=source.getnchannels(),
                sample_width=source.getsampwidth(),
                frames=source.readframes(source.getnframes()),
            )
    except (wave.Error, EOFError, struct.error, ValueError) as error:
        raise WavError("WAV として読み取れません。") from error


def encode_wav(audio: WavAudio) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as sink:
        sink.setnchannels(audio.channels)
        sink.setsampwidth(audio.sample_width)
        sink.setframerate(audio.sample_rate)
        sink.writeframes(audio.frames)
    return buffer.getvalue()


def tone_wav(seconds: float, frequency: float = 440.0) -> bytes:
    """検証用の決定的な 16 kHz モノラル WAV を作ります。"""
    sample_count = max(1, int(DEVICE_SAMPLE_RATE * seconds))
    frames = bytearray()
    for index in range(sample_count):
        value = int(8_000 * math.sin(2 * math.pi * frequency * index / DEVICE_SAMPLE_RATE))
        frames += struct.pack("<h", value)
    return encode_wav(
        WavAudio(
            sample_rate=DEVICE_SAMPLE_RATE,
            channels=DEVICE_CHANNELS,
            sample_width=DEVICE_SAMPLE_WIDTH,
            frames=bytes(frames),
        )
    )
