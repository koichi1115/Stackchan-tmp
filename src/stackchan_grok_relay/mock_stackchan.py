import argparse
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .domain import SpokenReply
from .stackchan import MockStackChanListener, MockStackChanSpeaker


def main() -> int:
    parser = argparse.ArgumentParser(description="モック StackChan から発話を一件送ります。")
    parser.add_argument("text", nargs="?", default="こんにちは", help="送信する発話")
    args = parser.parse_args()

    listener = MockStackChanListener(args.text)
    speaker = MockStackChanSpeaker()
    utterance = listener.receive()
    if utterance is None:
        print("送信する発話がありません。", file=sys.stderr)
        return 2

    relay_url = os.getenv("RELAY_URL", "http://127.0.0.1:8787/utterance")
    request = Request(
        relay_url,
        data=json.dumps({"text": utterance.text}, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=5) as response:
            body = json.loads(response.read().decode("utf-8"))
        if not isinstance(body, dict) or not isinstance(body.get("speak"), str):
            raise ValueError
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError, ValueError):
        print("リレーから有効な応答を受け取れませんでした。", file=sys.stderr)
        return 1

    speaker.speak(SpokenReply(speak=body["speak"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
