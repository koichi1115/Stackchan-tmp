import contextlib
import io
import unittest

from stackchan_grok_relay.domain import SpokenReply
from stackchan_grok_relay.stackchan import MockStackChanSpeaker


class MockStackChanSpeakerTests(unittest.TestCase):
    def test_empty_reply_stays_silent(self) -> None:
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            MockStackChanSpeaker().speak(SpokenReply(speak=""))

        self.assertEqual(output.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
