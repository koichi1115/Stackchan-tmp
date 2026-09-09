import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        expected_key = "test-only-placeholder"
        if (
            self.headers.get("Authorization") != f"Bearer {expected_key}"
            or self.headers.get("X-Automation-Key") != expected_key
        ):
            self.send_error(401)
            return

        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        if set(body) != {"text"}:
            self.send_error(400)
            return
        reply = "こんにちは。" if body.get("text") == "こんにちは" else "短い返答です。"
        payload = json.dumps({"reply": reply}, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser(description="オフライン検証用のモック Webhook です。")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"モック Webhook を http://{args.host}:{args.port} で起動しました。", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
