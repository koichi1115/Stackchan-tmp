"""Grok Webhook routine が受け付ける形を調べます。鍵と URL のパスは表示しません。

使い方（リポジトリの直下で、.env のあるディレクトリ）:
    python3 scripts/probe-grok-webhook.py [--env .env] [--text こんにちは]

各バリアントについて HTTP ステータス、Content-Type、本文の先頭 300 文字を表示します。
routine が実行される形もあるので、何度も回さないでください。
"""

import argparse
import json
import os
from pathlib import Path
import socket
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def call(url: str, method: str, body: bytes | None, headers: dict[str, str], timeout: float) -> str:
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            text = response.read(4_096).decode("utf-8", "replace")
            return f"{response.status} {response.headers.get('Content-Type', '-')} | {' '.join(text.split())[:300]}"
    except HTTPError as error:
        text = ""
        try:
            text = error.read(4_096).decode("utf-8", "replace")
        except (OSError, ValueError):
            pass
        interesting = {k: v for k, v in error.headers.items() if k.lower() in ("content-type", "www-authenticate", "x-request-id", "cf-ray", "server", "allow")}
        return f"{error.code} {interesting} | {' '.join(text.split())[:300]}"
    except (TimeoutError, socket.timeout):
        return "timeout"
    except URLError as error:
        return f"URLError {error.reason}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", type=Path, default=Path(".env"))
    parser.add_argument("--text", default="こんにちは")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()

    env = load_env(args.env) if args.env.exists() else {}
    url = os.getenv("GROK_WEBHOOK_URL") or env.get("GROK_WEBHOOK_URL", "")
    key = os.getenv("GROK_WEBHOOK_SENDER_KEY") or env.get("GROK_WEBHOOK_SENDER_KEY", "")
    if not url or not key:
        print("GROK_WEBHOOK_URL / GROK_WEBHOOK_SENDER_KEY が見つかりません。", file=sys.stderr)
        return 2

    parsed = urlparse(url)
    print(f"URL: {parsed.scheme}://{parsed.hostname} path={len(parsed.path)}文字 query={'あり' if parsed.query else 'なし'} 末尾空白={'あり' if url != url.strip() else 'なし'}")
    print(f"鍵: {len(key)} 文字, 先頭 4 文字 {key[:4]}…, 空白含む={'はい' if key != key.strip() or ' ' in key else 'いいえ'}")
    url = url.strip()
    key = key.strip()
    text = args.text

    bearer = {"Authorization": f"Bearer {key}"}
    both = {**bearer, "X-Automation-Key": key}
    json_ct = {"Content-Type": "application/json"}
    json_ct_charset = {"Content-Type": "application/json; charset=utf-8"}
    ua = {"User-Agent": "stackchan-grok-relay/0.1"}

    def j(obj: object) -> bytes:
        return json.dumps(obj, ensure_ascii=False).encode("utf-8")

    variants: list[tuple[str, str, bytes | None, dict[str, str]]] = [
        ("A 現行: {text} + charset + 両ヘッダー", "POST", j({"text": text}), {**json_ct_charset, **both}),
        ("B {text} + charset なし + Bearer のみ", "POST", j({"text": text}), {**json_ct, **bearer}),
        ("C {text} + User-Agent 付き", "POST", j({"text": text}), {**json_ct, **bearer, **ua}),
        ("D {message}", "POST", j({"message": text}), {**json_ct, **bearer}),
        ("E {input}", "POST", j({"input": text}), {**json_ct, **bearer}),
        ("F {prompt}", "POST", j({"prompt": text}), {**json_ct, **bearer}),
        ("G {content}", "POST", j({"content": text}), {**json_ct, **bearer}),
        ("H {payload:{text}}", "POST", j({"payload": {"text": text}}), {**json_ct, **bearer}),
        ("I {} 空オブジェクト", "POST", j({}), {**json_ct, **bearer}),
        ("J text/plain 本文", "POST", text.encode("utf-8"), {"Content-Type": "text/plain; charset=utf-8", **bearer}),
        ("K form-urlencoded text=", "POST", urlencode({"text": text}).encode("ascii"), {"Content-Type": "application/x-www-form-urlencoded", **bearer}),
        ("L 鍵なし {text}（認証の有無を見る）", "POST", j({"text": text}), {**json_ct}),
        ("M GET（許可メソッドの確認）", "GET", None, {**bearer}),
    ]
    for label, method, body, headers in variants:
        print(f"[{label}]")
        print("   ", call(url, method, body, headers, args.timeout))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
