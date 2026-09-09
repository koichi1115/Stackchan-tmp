"""非公式ファームウェアのプロトコルをそのまま再現する検証用クライアント。

実機と同じ手順で、録音した WAV を送り、返ってきた WAV を「再生」します。
実機の代わりに標準出力へ `SPOKEN: <発話>` を出します。無音のときは何も出しません。
"""

import argparse
import base64
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .device import SPOKEN_TEXT_HEADER
from .wav import WavError, decode_wav


DEFAULT_FIXTURE = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "utterance.wav"


def main() -> int:
    parser = argparse.ArgumentParser(description="実機プロトコルの検証用シミュレーターです。")
    parser.add_argument(
        "--relay-url",
        default="http://127.0.0.1:8787/device/utterance",
        help="リレーの端末向けエンドポイント",
    )
    parser.add_argument("--wav", type=Path, default=DEFAULT_FIXTURE, help="送信する録音 WAV")
    parser.add_argument("--save-reply", type=Path, default=None, help="受け取った音声の保存先")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()

    try:
        audio = args.wav.read_bytes()
    except OSError as error:
        print(f"録音を読み込めませんでした。{error}", file=sys.stderr)
        return 2

    request = Request(
        args.relay_url,
        data=audio,
        headers={"Content-Type": "audio/wav", "Accept": "audio/wav"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=args.timeout) as response:
            status = response.status
            spoken_header = response.headers.get(SPOKEN_TEXT_HEADER, "")
            body = response.read()
    except (HTTPError, URLError, TimeoutError) as error:
        print(f"リレーから応答を受け取れませんでした。{error}", file=sys.stderr)
        return 1

    if status == 204 or not body:
        print("無音です。何も再生しません。", file=sys.stderr)
        return 0

    try:
        reply_audio = decode_wav(body)
    except WavError:
        print("受け取った応答が WAV ではありません。再生しません。", file=sys.stderr)
        return 1

    try:
        spoken = base64.b64decode(spoken_header, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        print("発話テキストのヘッダーを読み取れませんでした。", file=sys.stderr)
        return 1

    if args.save_reply is not None:
        args.save_reply.write_bytes(body)

    print(
        f"再生: {reply_audio.frame_count} フレーム "
        f"{reply_audio.sample_rate} Hz {reply_audio.channels} ch",
        file=sys.stderr,
    )
    print(f"SPOKEN: {spoken}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
