import math
import struct
import unittest

from stackchan_grok_relay.vad import FRAME_SAMPLES, SpeechSegmenter, frame_rms


def tone_frame(amplitude: int = 8_000) -> bytes:
    return b"".join(
        struct.pack("<h", int(amplitude * math.sin(2 * math.pi * 440 * index / 16_000)))
        for index in range(FRAME_SAMPLES)
    )


def silent_frame() -> bytes:
    return bytes(FRAME_SAMPLES * 2)


def feed_all(segmenter: SpeechSegmenter, frames: list[bytes]) -> list[bytes]:
    segments = []
    for frame in frames:
        segment = segmenter.feed(frame)
        if segment is not None:
            segments.append(segment)
    return segments


class SpeechSegmenterTests(unittest.TestCase):
    def test_rms_of_silence_is_zero(self) -> None:
        self.assertEqual(frame_rms(silent_frame()), 0.0)
        self.assertGreater(frame_rms(tone_frame()), 5_000)

    def test_returns_one_segment_after_speech_then_silence(self) -> None:
        segmenter = SpeechSegmenter(threshold=600, silence_ms=700)
        frames = [silent_frame()] * 5 + [tone_frame()] * 10 + [silent_frame()] * 15
        segments = feed_all(segmenter, frames)
        self.assertEqual(len(segments), 1)
        # 先読み分（最大 5 フレーム）と発話 10 フレーム、無音 11 フレームぶんが含まれる
        self.assertGreaterEqual(len(segments[0]), 10 * FRAME_SAMPLES * 2)
        self.assertFalse(segmenter.in_speech)

    def test_short_burst_is_discarded(self) -> None:
        segmenter = SpeechSegmenter(threshold=600, silence_ms=700, min_speech_ms=300)
        frames = [tone_frame()] * 2 + [silent_frame()] * 15
        self.assertEqual(feed_all(segmenter, frames), [])

    def test_silence_alone_never_yields(self) -> None:
        segmenter = SpeechSegmenter()
        self.assertEqual(feed_all(segmenter, [silent_frame()] * 200), [])

    def test_long_speech_is_cut_at_max_seconds(self) -> None:
        segmenter = SpeechSegmenter(threshold=600, max_seconds=1.0)
        frames = [tone_frame()] * 40  # 40 × 64 ms = 2.56 s
        segments = feed_all(segmenter, frames)
        self.assertGreaterEqual(len(segments), 1)
        self.assertLessEqual(len(segments[0]), int(1.0 * 16_000) * 2 + 6 * FRAME_SAMPLES * 2)

    def test_reset_clears_state(self) -> None:
        segmenter = SpeechSegmenter(threshold=600)
        for _ in range(5):
            segmenter.feed(tone_frame())
        self.assertTrue(segmenter.in_speech)
        segmenter.reset()
        self.assertFalse(segmenter.in_speech)
        self.assertEqual(feed_all(segmenter, [silent_frame()] * 20), [])

    def test_noise_floor_raises_threshold_slowly_and_speech_does_not_lift_it(self) -> None:
        segmenter = SpeechSegmenter(threshold=100, noise_multiplier=2.5)
        noise = tone_frame(amplitude=1_000)
        for _ in range(3_000):  # 約 3 分ぶんの定常雑音
            segmenter.feed(noise)
        self.assertGreater(segmenter.effective_threshold, 1_000)

        quiet = SpeechSegmenter(threshold=600)
        for _ in range(20):
            quiet.feed(silent_frame())
        for _ in range(10):
            quiet.feed(tone_frame())
        # 発話 10 フレーム（0.64 秒）では床はほとんど動かない
        self.assertLess(quiet.effective_threshold, 700)
