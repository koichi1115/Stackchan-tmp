import sys

from .config import Config, ConfigError
from .relay import build_server


def main() -> int:
    try:
        config = Config.from_env()
        server = build_server(config)
    except (ConfigError, ValueError) as error:
        print(f"設定エラー。{error}", file=sys.stderr)
        return 2

    print(f"リレーを http://{config.host}:{config.port} で起動しました。", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("リレーを停止しました。")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
