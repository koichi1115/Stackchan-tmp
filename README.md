# stackchan-grok-relay

`stackchan-grok-relay` は、StackChan の発話を自宅のリレーで受け、Grok Bot の Webhook に問い合わせ、短い一文だけを実機に喋らせる小さなリレーです。**家のネットワークに公開受信口を一つも作りません。** 認証情報はすべてリレー側にあり、ロボットが持つのは Wi-Fi 資格情報とリレーの LAN アドレスだけです。

現在の構成（s4）は常時起動の Mac をリレーにし、ウェイクワード「スタックちゃん」で会話が始まり、Grok からの読み上げ（家族チャット）にも対応します。設計は [`docs/always-on-mac.md`](docs/always-on-mac.md) にあります。

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

リレーの受信口は三つで、いずれもプライベート網の内側だけにあります。

| 受信口 | 用途 | 入出力 |
| --- | --- | --- |
| `POST /utterance` | s1 からのテキスト経路（変更なし） | `{"text":"..."}` → `{"speak":"..."}` |
| `POST /device/utterance` | s3 の実機向け音声経路（一件ずつ） | `audio/wav` → `audio/wav` と `X-Spoken-Text-Base64` ヘッダー |
| `GET /device/stream`（WebSocket） | s4 の実機向け常時接続。マイク音声を流し、ウェイクワード判定と発話音声の送り下ろしを行う | PCM フレーム ⇄ `state` / `speak` + WAV（[プロトコル](docs/always-on-mac.md#ロボット--mac-の-websocket-プロトコル)） |

Grok からの読み上げは、Cloudflare Worker の受信箱（[`worker/`](worker/)）に Grok が投函し、リレーが外向き HTTPS で取りに行きます。家のネットワークには何も開きません。

会話の返答は既定では Grok Bot Webhook（`{"text":"..."}` を送り `{"reply":"..."}` を期待）ですが、**Webhook routine は起動確認だけを返す非同期型と分かったため、会話には `REPLY_ENGINE=grok_api`（xAI API の直接呼び出し、[`docs/always-on-mac.md`](docs/always-on-mac.md#返答の生成元reply_engine)）を使います。** Webhook へ送るのは `{"text":"..."}` だけです。sender key は `Authorization: Bearer <sender-key>` と `X-Automation-Key: <sender-key>` に入れます。**Webhook の空応答や失敗、認識できない録音は、すべて無音になります。** エラーを発話することはありません（`/device/utterance` は `204 No Content` を返します）。

経路の詳細は [`docs/always-on-mac.md`](docs/always-on-mac.md)（s4）と [`docs/real-device-path.md`](docs/real-device-path.md)（s3）にあります。

## 使い始める（s4、常時起動 Mac）

1. **バックアップ**（済んでいなければ）: ロボットを USB でつなぎ、`./scripts/backup-device.sh --port <ポート>` を実行し、成功行が出るまで先へ進みません（[`docs/backup-before-flash.md`](docs/backup-before-flash.md)）。
2. **Grok の会話用 routine**: [`prompt.txt`](prompt.txt) の内容を Webhook routine のプロンプトに設定し、Webhook URL と sender key を控えます。
3. **受信箱の Worker**: この PC で `npx wrangler login` を一度実行し、`worker/deploy.sh` で配置して、送信キーと巡回キーを設定します（[`worker/README.md`](worker/README.md)）。家族チャットの routine には [`worker/GROK_ROUTINE.md`](worker/GROK_ROUTINE.md) の手順を加えます。
4. **Mac の準備**: リモートログインを有効にし、この PC の公開鍵を `~/.ssh/authorized_keys` に加えます。`mac/deploy.sh --host <user@mac> --setup` で whisper.cpp、VOICEVOX ENGINE、リレーを launchd に登録します（[`mac/README.md`](mac/README.md)）。
5. **Mac の `.env`**: `cp .env.example .env` の上で、`RELAY_HOST` を Mac の LAN IPv4、`GROK_WEBHOOK_URL` / `GROK_WEBHOOK_SENDER_KEY`、`STT_ENGINE=whisper_http` / `STT_URL=http://127.0.0.1:8080/inference`、`TTS_ENGINE=voicevox_http` / `TTS_URL=http://127.0.0.1:50021`、`INBOX_URL` / `INBOX_POLL_KEY` を設定し、`launchctl kickstart -k gui/$(id -u)/jp.stackchan.relay` で再起動します。
6. **検証**: `./scripts/test-all.sh` を通し、`python3 -m stackchan_grok_relay.stream_sim --relay-url ws://<Mac>:8787/device/stream --wav <WAV>` で `SPOKEN:` が出ることを確認します。
7. **ロボット**: `cp firmware/include/config.example.h firmware/include/config.h` に Wi-Fi 資格情報と Mac の LAN アドレスを書き、`cd firmware && pio run -e cores3 -t upload --upload-port <ポート>` で書き込みます（env は基板に合わせます。[`firmware/README.md`](firmware/README.md)）。
8. 「スタックちゃん」と呼びかけ、「はい？」のあとに話します。一文だけ返ってきます。ボタン A でも同じことができます。
9. 家族チャットで Grok に「スタックちゃんに『ただいま』と言わせて」のように頼むと、数秒後にロボットが読み上げます。

純正へ戻すときは `./scripts/restore-device.sh --port /dev/ttyUSB0 --backup-dir backups/<タイムスタンプ>` です。

## 検証

| コマンド | 内容 |
| --- | --- |
| `./scripts/test-all.sh` | 単体テスト、オフライン結合検証、シークレット検査 |
| `PYTHONPATH=src python3 -m unittest discover -s tests -v` | 単体テストのみ |
| `./scripts/test-integration.sh` | モック Webhook・モック STT・モック TTS で一往復（テキスト経路、s3 の実機プロトコル、s4 の WebSocket 経路） |
| `PYTHONPATH=src python3 -m stackchan_grok_relay.stream_sim --relay-url ws://<host>:8787/device/stream --wav <WAV>` | s4 の WebSocket 経路を手で試す（16 kHz モノラル WAV を流す） |
| `./scripts/device-sim.sh` | 実機プロトコルだけを手で試す |
| `./scripts/secret-scan.sh` | `firmware/` を含むシークレット検査 |

`device_sim` は非公式ファームウェアと同じ手順（WAV を POST し、WAV と発話テキストを受け取る）で動きます。実機の代わりに `SPOKEN: <発話>` を表示します。

## 設定

`RELAY_HOST` の既定値 `127.0.0.1` は同じコンピューターからの接続だけを受け付けます。LAN のプライベート IPv4 と Tailscale の `100.64.0.0/10` も指定できますが、**公開アドレスと全インターフェース待受（`0.0.0.0`）は設定検査が拒否します。** 実機を使うときはリレー機の LAN アドレスに変更してください。

音声エンジンは差し替え可能です。既定は `mock` で、オフライン検証はこのまま行えます。実機では、家庭内で動かす鍵不要のエンジンを既定として想定しています（STT: whisper.cpp の HTTP サーバー、TTS: VOICEVOX ENGINE）。

whisper を使うときは `STT_PROMPT`（既定は `WAKE_WORDS` の先頭の一語）が whisper の初期プロンプトとして渡ります。**これが無いと「スタックちゃん」が「スタークちゃん」と書き起こされ、ウェイクワードが通りません。** 実測は [`docs/always-on-mac.md`](docs/always-on-mac.md) の「実音声で分かったこと」にあります。

## 脅威モデル

- `/utterance` と `/device/utterance` に認証はありません。**どちらも公開ネットワークへ露出させないでください。** ポート転送、トンネル、クラウド中継は使いません。設定検査が公開待受を拒否します。
- `/device/utterance` と `/device/stream` は無認証で音声を受け取ります。家庭内 LAN または tailnet に入れる端末を信頼する前提です。同じ網にいる誰かがこの受信口へ音声を送れば、Grok Bot への問い合わせを起こせます。これは受け入れているリスクで、その代わりに公開受信口を作りません。
- `/device/stream` に流れてくる音声のうち、ウェイクワードを含まない発話は文字起こしの後に捨てられ、Grok へは送られず、本文はログにも残りません。
- 受信箱の Worker は家の外（Cloudflare）にあり、投函用の送信キーと取り出し用の巡回キーを分けています。送信キーが漏れても読み上げ用の文を投函できるだけで、読み出しはできません。リレーは受信箱から**テキストだけ**を受け取り、制御文字を除き長さを制限してから合成します。
- 受信する音声には大きさの上限（`MAX_AUDIO_BYTES`、既定 1 MB）があり、WAV として読めないものは無音として捨てます。
- 発話本文と Webhook 応答を信頼しません。入力は境界で検査し、返答から制御文字と二文目以降を除き、長さを制限します。
- Webhook URL、sender key、STT・TTS の接続先、および将来鍵が必要なエンジンへ差し替えた場合のその鍵は、**すべてリレーの環境変数（`.env`）だけ**に置きます。`.env` は Git の対象外です。
- **ロボットには Wi-Fi 資格情報とリレーの LAN アドレスしか置きません。** API キー、トークン、sender key、Google の認証情報は端末に一切ありません。`firmware/include/config.h` は Git 管理外で、シークレット検査が `firmware/` も対象にします。
- 音声がロボットの外へ出る範囲は家庭内 LAN または tailnet の内側だけです。Grok Bot へ渡るのはテキストだけです。
- Webhook 呼び出しには時間制限があり、再試行は最大一回です。失敗時は構造化エラーを標準エラーへ記録し、発話を空にします。
- 会話履歴を保存しません。録音も返答音声も保存しません。各リクエストは独立しています。
- フラッシュのバックアップ（`backups/`）は端末固有情報を含みます。Git 管理外です。公開しないでください。

## 資料

- [`docs/always-on-mac.md`](docs/always-on-mac.md) — s4: 常時起動 Mac、ウェイクワード、受信箱の設計とプロトコル
- [`docs/real-device-path.md`](docs/real-device-path.md) — s3: 実機一往復の経路定義
- [`docs/backup-before-flash.md`](docs/backup-before-flash.md) — 書き込み前のバックアップ（必須）と復元
- [`firmware/README.md`](firmware/README.md) — 非公式ファームウェアのビルド・設定・書き込み
- [`docs/stackchan-world-hooks.md`](docs/stackchan-world-hooks.md) — s1 の純正フック調査
- [`BLOCKER.md`](BLOCKER.md) — 判断の経緯、選んだ経路、代償、未検証の点
