# stackchan-grok-relay

`stackchan-grok-relay` は、StackChan の一回の発話を自宅のリレーで受け、Grok Bot の Webhook に問い合わせ、短い一文だけを実機に喋らせる最小のリレーです。**公開受信口を一つも作りません。** 認証情報はすべてリレー側にあり、ロボットが持つのは Wi-Fi 資格情報とリレーの LAN アドレスだけです。

純正ファームウェアには必要なフックがないため、ロボット側は非公式ファームウェアに置き換えます。判断の経緯と代償は [`BLOCKER.md`](BLOCKER.md) にあります。

## 経路

```
人の声
  → ロボット（録音）
  → リレー /device/utterance     音声（WAV）
  → リレー内で音声認識（STT）
  → Grok Bot Webhook             テキストのみ
  → リレー内で一文にクランプ
  → リレー内で音声合成（TTS）
  → ロボット                     音声（WAV）
  → スピーカーから一文
```

リレーの受信口は二つで、どちらもプライベート網の内側だけにあります。

| 受信口 | 用途 | 入出力 |
| --- | --- | --- |
| `POST /utterance` | s1 からのテキスト経路（変更なし） | `{"text":"..."}` → `{"speak":"..."}` |
| `POST /device/utterance` | 実機向けの音声経路 | `audio/wav` → `audio/wav` と `X-Spoken-Text-Base64` ヘッダー |

Grok Bot Webhook へ送るのは `{"text":"..."}` だけで、`{"reply":"..."}` を受け取ります。sender key は `Authorization: Bearer <sender-key>` と `X-Automation-Key: <sender-key>` に入れます。**Webhook の空応答や失敗、認識できない録音は、すべて無音になります。** エラーを発話することはありません（`/device/utterance` は `204 No Content` を返します）。

経路の詳細は [`docs/real-device-path.md`](docs/real-device-path.md) にあります。

## 使い始める（10 手順）

1. Python 3.11 以降を用意し、`cp .env.example .env` を実行します。
2. [`prompt.txt`](prompt.txt) の内容を Grok Bot Webhook routine のプロンプトに設定します。HTTP body には含めません。
3. routine の画面から Webhook URL と sender key を取得し、`.env` の `GROK_WEBHOOK_URL` と `GROK_WEBHOOK_SENDER_KEY` に設定します。
4. `./scripts/test-all.sh` を実行します。実キー無しで全経路（`SPOKEN: こんにちは。` を含む）を検証します。
5. **ロボットを USB でつなぎ、`./scripts/backup-device.sh --port /dev/ttyUSB0` を実行します。** 成功行が出るまで先へ進まないでください（[`docs/backup-before-flash.md`](docs/backup-before-flash.md)）。
6. `cp firmware/include/config.example.h firmware/include/config.h` を実行し、Wi-Fi 資格情報とリレーの LAN アドレスだけを書きます。
7. `cd firmware && pio run -e core2 -t upload --upload-port /dev/ttyUSB0` で書き込みます（env は基板に合わせます。[`firmware/README.md`](firmware/README.md)）。
8. リレー機で音声エンジンを起動し、`.env` に `STT_ENGINE=whisper_http` / `STT_URL`、`TTS_ENGINE=voicevox_http` / `TTS_URL` を設定します。
9. `.env` の `RELAY_HOST` をリレー機のプライベート IPv4（例 `192.168.1.20`）にして `./scripts/run-relay.sh` を実行します。
10. ロボットのボタン A を押して一言話します。一文だけ返ってきます。

純正へ戻すときは `./scripts/restore-device.sh --port /dev/ttyUSB0 --backup-dir backups/<タイムスタンプ>` です。

## 検証

| コマンド | 内容 |
| --- | --- |
| `./scripts/test-all.sh` | 単体テスト、オフライン結合検証、シークレット検査 |
| `PYTHONPATH=src python3 -m unittest discover -s tests -v` | 単体テストのみ |
| `./scripts/test-integration.sh` | モック Webhook・モック STT・モック TTS で一往復（テキスト経路と実機プロトコル） |
| `./scripts/device-sim.sh` | 実機プロトコルだけを手で試す |
| `./scripts/secret-scan.sh` | `firmware/` を含むシークレット検査 |

`device_sim` は非公式ファームウェアと同じ手順（WAV を POST し、WAV と発話テキストを受け取る）で動きます。実機の代わりに `SPOKEN: <発話>` を表示します。

## 設定

`RELAY_HOST` の既定値 `127.0.0.1` は同じコンピューターからの接続だけを受け付けます。LAN のプライベート IPv4 と Tailscale の `100.64.0.0/10` も指定できますが、**公開アドレスと全インターフェース待受（`0.0.0.0`）は設定検査が拒否します。** 実機を使うときはリレー機の LAN アドレスに変更してください。

音声エンジンは差し替え可能です。既定は `mock` で、オフライン検証はこのまま行えます。実機では、家庭内で動かす鍵不要のエンジンを既定として想定しています（STT: whisper.cpp の HTTP サーバー、TTS: VOICEVOX ENGINE）。

## 脅威モデル

- `/utterance` と `/device/utterance` に認証はありません。**どちらも公開ネットワークへ露出させないでください。** ポート転送、トンネル、クラウド中継は使いません。設定検査が公開待受を拒否します。
- `/device/utterance` は無認証で音声を受け取ります。家庭内 LAN または tailnet に入れる端末を信頼する前提です。同じ網にいる誰かがこの受信口へ音声を送れば、Grok Bot への問い合わせを一回起こせます。これは受け入れているリスクで、その代わりに公開受信口を作りません。
- 受信する音声には大きさの上限（`MAX_AUDIO_BYTES`、既定 1 MB）があり、WAV として読めないものは無音として捨てます。
- 発話本文と Webhook 応答を信頼しません。入力は境界で検査し、返答から制御文字と二文目以降を除き、長さを制限します。
- Webhook URL、sender key、STT・TTS の接続先、および将来鍵が必要なエンジンへ差し替えた場合のその鍵は、**すべてリレーの環境変数（`.env`）だけ**に置きます。`.env` は Git の対象外です。
- **ロボットには Wi-Fi 資格情報とリレーの LAN アドレスしか置きません。** API キー、トークン、sender key、Google の認証情報は端末に一切ありません。`firmware/include/config.h` は Git 管理外で、シークレット検査が `firmware/` も対象にします。
- 音声がロボットの外へ出る範囲は家庭内 LAN または tailnet の内側だけです。Grok Bot へ渡るのはテキストだけです。
- Webhook 呼び出しには時間制限があり、再試行は最大一回です。失敗時は構造化エラーを標準エラーへ記録し、発話を空にします。
- 会話履歴を保存しません。録音も返答音声も保存しません。各リクエストは独立しています。
- フラッシュのバックアップ（`backups/`）は端末固有情報を含みます。Git 管理外です。公開しないでください。

## 資料

- [`docs/real-device-path.md`](docs/real-device-path.md) — 実機一往復の経路定義
- [`docs/backup-before-flash.md`](docs/backup-before-flash.md) — 書き込み前のバックアップ（必須）と復元
- [`firmware/README.md`](firmware/README.md) — 非公式ファームウェアのビルド・設定・書き込み
- [`docs/stackchan-world-hooks.md`](docs/stackchan-world-hooks.md) — s1 の純正フック調査
- [`BLOCKER.md`](BLOCKER.md) — 判断の経緯、選んだ経路、代償、未検証の点
