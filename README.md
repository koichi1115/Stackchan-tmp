# stackchan-grok-relay

`stackchan-grok-relay` は、StackChan の一回の発話テキストを自宅リレーから Grok Bot の Webhook へ渡し、短い一文だけを発話用テキストとして返す最小のステートレスリレーです。StackChan World に必要な公開フックがないため、現時点では実機へ接続せず、モックだけで全経路を検証します。

## 構成

`ロボット -> リレー -> Grok Bot -> リレー -> ロボット`

リレーは `POST /utterance` で `{"text":"..."}` を受け取り、`{"speak":"..."}` を返します。Grok Bot Webhook には `{"text":"..."}` だけを送り、このスライスの応答契約である `{"reply":"..."}` を受け取ります。送信時は同じ sender key を `Authorization: Bearer <sender-key>` と `X-Automation-Key: <sender-key>` に設定します。Webhook の空応答または失敗時は `speak` を空にし、モック話者も何も表示しません。

## ローカルで動かす

1. Python 3.11 以降を用意します。
2. `cp .env.example .env` を実行します。
3. [`prompt.txt`](prompt.txt) の内容を Grok Bot Webhook routine のプロンプトに設定します。これは HTTP body には含めません。
4. routine の画面から Webhook URL と sender key を取得し、リレー上の `.env` に `GROK_WEBHOOK_URL` と `GROK_WEBHOOK_SENDER_KEY` として設定します。
5. `./scripts/run-relay.sh` を実行します。この一つのコマンドでリレーが起動します。
6. 別の端末で `./scripts/mock-stackchan.sh こんにちは` を実行します。
7. 実キーを使わず全経路を試す場合は `./scripts/test-integration.sh` を実行します。出力は `SPOKEN: こんにちは。` です。

初期値の `RELAY_HOST=127.0.0.1` は同じコンピューターからの接続だけを受け付けます。LAN のプライベート IPv4 アドレスまたは Tailscale の `100.64.0.0/10` も指定できますが、公開アドレスと全インターフェース待受は設定検査で拒否します。

## 検証

全検証は `./scripts/test-all.sh`、単体テストだけなら `PYTHONPATH=src python3 -m unittest discover -s tests -v`、シークレット検査だけなら `./scripts/secret-scan.sh` で実行します。

## 脅威モデル

- `/utterance` に認証はありません。公開ネットワークへ露出させないでください。
- 発話本文と Webhook 応答を信頼しません。入力は境界で検査し、返答から制御文字と二文目以降を除き、長さを制限します。
- Webhook URL、sender key、その他の Bot 認証情報はリレーの環境変数だけで渡します。`.env` は Git の対象外です。
- sender key と Bot 認証情報は、ロボット、ブラウザー、モッククライアント、端末へ配布する設定には置きません。Google のトークンや認証情報は使用しません。
- Webhook 呼び出しには時間制限があり、再試行は最大一回です。失敗時は構造化エラーを記録し、発話を空にします。
- 会話履歴を保存しません。各リクエストは独立しています。

実機接続の調査結果は [`docs/stackchan-world-hooks.md`](docs/stackchan-world-hooks.md) にあります。未対応経路は [`BLOCKER.md`](BLOCKER.md) にあります。
