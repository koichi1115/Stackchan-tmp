import unittest

from stackchan_grok_relay.domain import clamp_reply


class ClampReplyTests(unittest.TestCase):
    def test_keeps_only_first_sentence(self) -> None:
        self.assertEqual(clamp_reply("最初です。次です。", 120), "最初です。")

    def test_empty_reply_stays_empty(self) -> None:
        self.assertEqual(clamp_reply(" \n\t", 120), "")

    def test_strips_newlines_and_control_characters(self) -> None:
        self.assertEqual(clamp_reply("一行目\n二行目\u0000", 120), "一行目 二行目")

    def test_limits_length(self) -> None:
        self.assertEqual(clamp_reply("あ" * 121, 120), "あ" * 120)


if __name__ == "__main__":
    unittest.main()
