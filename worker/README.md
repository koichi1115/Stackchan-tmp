# stackchan-inbox（Cloudflare Worker 受信箱）

Grok Bot が投函し、常時起動 Mac のリレーが取り出す、**テキスト専用**の小さな郵便受けです。家のネットワークには何も開けません。音声は通りません。全体の位置づけは [`../docs/always-on-mac.md`](../docs/always-on-mac.md) の「Cloudflare Worker 受信箱」にあります。

| メソッドとパス | 鍵 | 内容 |
| --- | --- | --- |
| `POST /messages` | 送信キー `INBOX_SEND_KEY` | `{"text":"…"}` を保存。制御文字を除き、空白をまとめ、500 文字で切る。空なら 400。応答 `201 {"id":n}` |
| `GET /messages?after=<id>&limit=<n>` | 巡回キー `INBOX_POLL_KEY` | 未読（未 ack）を `id` の古い順に。`limit` は 1〜50、既定 20。応答 `{"messages":[{"id","text","created_at"}]}` |
| `POST /messages/{id}/ack` | 巡回キー | 読み上げ済みにする。`200 {"id":n,"acked":true}`。無い id は 404 |
| `GET /healthz` | 不要 | `ok` |

- 鍵は `Authorization: Bearer <キー>` で渡します。送信キーでは読めず、巡回キーでは投函できません。無い・違う → `401`。
- 二つの secret のどちらかが未設定なら、全経路が `503` になります（設定途中で開いたままにしません）。
- 本文は 8 KB まで。応答はすべて `Cache-Control: no-store`。
- 依存ライブラリはありません。ファイルは `wrangler.toml`、`schema.sql`、`src/index.js` の三つです。

## ローカルで試す（Cloudflare へのログイン不要）

```bash
cd worker
printf 'INBOX_SEND_KEY=dev-send\nINBOX_POLL_KEY=dev-poll\n' > .dev.vars   # Git 管理外
npx --yes wrangler@4 d1 execute stackchan-inbox --local --file schema.sql
npx --yes wrangler@4 dev --local --port 8788
```

別の端末から:

```bash
BASE=http://127.0.0.1:8788
curl -sS $BASE/healthz; echo
curl -sS -X POST $BASE/messages -H 'Content-Type: application/json' -d '{"text":"鍵なし"}'; echo       # 401
curl -sS -X POST $BASE/messages -H 'Authorization: Bearer dev-send' -H 'Content-Type: application/json' -d '{"text":"こんにちは"}'; echo   # 201 {"id":1}
curl -sS "$BASE/messages" -H 'Authorization: Bearer dev-send'; echo                                                # 401（送信キーでは読めない）
curl -sS "$BASE/messages" -H 'Authorization: Bearer dev-poll'; echo                                                # {"messages":[…]}
curl -sS -X POST $BASE/messages/1/ack -H 'Authorization: Bearer dev-poll'; echo                                    # {"id":1,"acked":true}
curl -sS "$BASE/messages" -H 'Authorization: Bearer dev-poll'; echo                                                # {"messages":[]}
```

ローカルの D1 は `worker/.wrangler/` に置かれます（Git 管理外）。

## 配置する（所有者の PC で）

1. 一度だけ `npx --yes wrangler@4 login` を実行し、ブラウザで許可します。`./deploy.sh --check` で `whoami` が通れば準備完了です。
2. `./deploy.sh` を実行します。`schema.sql` を D1 に適用（既存の行には触れません）し、Worker を配置し、secret の設定状況を表示します。
3. 初回は secret を二つ入れます。値はそれぞれ `python3 -c "import secrets; print(secrets.token_urlsafe(32))"` で生成します。

   ```bash
   npx --yes wrangler@4 secret put INBOX_SEND_KEY
   npx --yes wrangler@4 secret put INBOX_POLL_KEY
   ```

4. `deploy` が表示した URL（`https://stackchan-inbox.<account>.workers.dev`）を確認します。
   - `INBOX_SEND_KEY` と URL は Grok routine へ（[`GROK_ROUTINE.md`](GROK_ROUTINE.md)）。
   - `INBOX_POLL_KEY` と URL は Mac の `.env`（`INBOX_POLL_KEY`、`INBOX_URL`）へ。
5. `curl -sS https://stackchan-inbox.<account>.workers.dev/healthz` が `ok` を返せば完了です。

キーを入れ替えるときは `secret put` をやり直すだけです。再配置は不要です。
