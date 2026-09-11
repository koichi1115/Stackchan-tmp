import unittest

from stackchan_grok_relay.wake import WakeWordMatcher, clean_transcript, normalize


class NormalizeTests(unittest.TestCase):
    def test_katakana_hiragana_and_punctuation(self) -> None:
        self.assertEqual(normalize("スタックちゃん"), "すたっくちゃん")
        self.assertEqual(normalize("すたっく ちゃん、"), "すたっくちゃん")
        self.assertEqual(normalize("ＳｔａｃｋＣｈａｎ！"), "stackchan")
        self.assertEqual(normalize("コーヒー"), "こーひー")

    def test_clean_transcript_drops_artifacts(self) -> None:
        self.assertEqual(clean_transcript("[BLANK_AUDIO]"), "")
        self.assertEqual(clean_transcript("(音楽)"), "")
        self.assertEqual(clean_transcript("（拍手）"), "")
        self.assertEqual(clean_transcript("  こんにちは  "), "こんにちは")


class WakeWordMatcherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.matcher = WakeWordMatcher()

    def test_wake_word_with_command(self) -> None:
        match = self.matcher.match("スタックちゃん、今日の天気は？")
        self.assertTrue(match.matched)
        self.assertEqual(match.remainder, "今日の天気は？")

    def test_wake_word_alone(self) -> None:
        match = self.matcher.match("すたっくちゃん。")
        self.assertTrue(match.matched)
        self.assertEqual(match.remainder, "")

    def test_variants_and_spacing(self) -> None:
        for text in ("スタックチャン 教えて", "スタック ちゃん教えて", "ねえスタックちゃん、教えて"):
            match = self.matcher.match(text)
            self.assertTrue(match.matched, text)
            self.assertEqual(match.remainder, "教えて", text)

    def test_no_wake_word(self) -> None:
        match = self.matcher.match("今日は寒いね")
        self.assertFalse(match.matched)
        self.assertEqual(match.remainder, "")

    def test_wake_word_too_far_from_start_is_ignored(self) -> None:
        match = self.matcher.match("さっきの話だけどスタックちゃんは元気？")
        self.assertFalse(match.matched)

    def test_custom_words(self) -> None:
        matcher = WakeWordMatcher(words=("ロボ",))
        match = matcher.match("ろぼ、こんにちは")
        self.assertTrue(match.matched)
        self.assertEqual(match.remainder, "こんにちは")
