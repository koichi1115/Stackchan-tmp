# 常時起動 Mac を中継にした会話経路（s4）

s3 までの「ボタン A を押して一往復」を、次の三つができる構成へ置き換えます。

1. **ウェイクワードで起動する。** 「スタックちゃん」と呼びかけると聞き取り状態になる。
2. **Grok Bot からの読み上げ。** 家族チャットで「スタックちゃんに伝えて」と頼まれた Grok が、ロボットに読み上げさせる。
3. **人との会話。** ウェイクワード → STT → Grok Bot → TTS → ロボットが一文返す。

リレーはこの PC ではなく、**常時起動している Mac（Apple Silicon）** で動かします。Mac が STT（whisper.cpp）、TTS（VOICEVOX ENGINE）、ウェイクワード判定、Grok との通信、受信箱の巡回をすべて担います。ロボットは「マイク音声を Mac へ流す」「Mac から届いた音声を鳴らす」「顔を表示する」だけです。

## s3 からの変更点と、変えない点

| 項目 | s3 | s4 |
| --- | --- | --- |
| 起動のきっかけ | ボタン A | ウェイクワード（ボタン A も残す） |
| ロボット → リレー | 録音一件を HTTP POST | WebSocket で常時ストリーミング（ロボットが接続を開始） |
| リレー → ロボット | HTTP 応答 | 同じ WebSocket で音声を送り下ろす |
| Grok → ロボット | なし | Cloudflare Worker の受信箱に Grok が投函し、Mac が外向き HTTPS で取りに行く |
| リレーの場所 | この PC | 常時起動 Mac |
| STT / TTS | モック | whisper.cpp / VOICEVOX ENGINE（Mac 上、鍵不要） |

変えない点:

- **家のネットワークに受信口を開けない。** ポート転送、トンネル、DDNS は使わない。Mac のリレーが待ち受けるのは LAN 内のプライベートアドレスだけで、接続してくるのはロボットだけ。
- **ロボットに鍵を置かない。** ロボットが持つのは Wi-Fi 資格情報と Mac の LAN アドレスだけ。
- **音声は家の外に出さない。** マイク音声は LAN 内の Mac までしか届かない。Grok へ渡るのはテキストだけ。ウェイクワードを含まない発話は、文字起こしの後に捨てられ、どこにも送られない。
- **失敗時は黙る。** エラーを発話しない。

新たに緩めた点は一つだけです。**Grok からの読み上げのために、インターネット上に受信箱（Cloudflare Worker）を一つ置きます。** これは家の外にある小さな郵便受けで、投函には送信キー、取り出しには別の巡回キーが要ります。家のネットワークには何も開きません。

## 全体の経路

```
[会話]
人の声 → ロボットのマイク → (WS, LAN) → Mac: VAD で区切る → whisper.cpp で文字起こし
      → ウェイクワードを含む発話だけ Grok Webhook へテキスト送信（HTTPS、外向き）
      → 返答一文 → VOICEVOX で合成 → (WS, LAN) → ロボットのスピーカー

[読み上げ]
家族チャット → Grok Bot routine → (HTTPS) Cloudflare Worker 受信箱に投函
      → Mac が数秒おきに外向き HTTPS で取り出し → VOICEVOX → (WS, LAN) → ロボット
```

## ロボット ⇄ Mac の WebSocket プロトコル

- URL: `ws://<RELAY_HOST>:<RELAY_PORT>/device/stream`。ロボットが接続を開始し、切れたら 3 秒後に再接続します。
- 音声はすべて **16 kHz・モノラル・16 bit リトルエンディアン PCM** です。

### ロボット → Mac

| 種別 | 内容 |
| --- | --- |
| テキスト | `{"type":"hello","device":"cores3","sample_rate":16000,"firmware":"0.2.0"}` 接続直後に一度 |
| バイナリ | 生 PCM。1 フレーム 1024 サンプル（2048 バイト）。聞き取り状態のあいだ連続して送る。再生中は送らない |
| テキスト | `{"type":"button","name":"A"}` ボタン A が押された（ウェイクワードの代わり） |
| テキスト | `{"type":"played"}` 受け取った音声を鳴らし終えた |

### Mac → ロボット

| 種別 | 内容 |
| --- | --- |
| テキスト | `{"type":"state","state":"listening"}` 顔の表情の指示。`listening` / `awake` / `thinking` / `speaking` のいずれか |
| テキスト | `{"type":"speak","text":"…","bytes":N}` これから WAV を N バイト送る |
| バイナリ | 上の WAV 本体。8192 バイトずつ分割して送る。ロボットは合計 N バイトになるまで連結してから鳴らす |

ロボットは `speak` を受けたらマイク送信を止め、WAV が揃ったら鳴らし、`played` を返してからマイク送信を再開します（半二重）。`text` は表示用で、ロボットはこれを解釈しません。

WAV は 16 kHz でなくても構いません（VOICEVOX は 24 kHz）。ロボットは WAV ヘッダーのサンプルレートで鳴らします。上限は 2 MB です。

## Mac 側の判定の流れ

1. **区切り（VAD）**: 音量が閾値を超えた状態が 100 ms 続いたら発話開始、700 ms 無音で発話終了。最長 12 秒。純 Python で実装し、外部依存を増やしません。
2. **文字起こし**: 区切った音声を WAV にして whisper.cpp の `/inference` へ渡す（LAN 内、鍵不要）。
3. **ウェイクワード判定**: 文字起こしを NFKC 正規化し、カタカナをひらがなに寄せ、句読点と空白を除いた上で、`WAKE_WORDS`（既定 `スタックちゃん,すたっくちゃん,スタックチャン`）のいずれかで**始まる**かを見る。
   - ウェイクワードの後ろに本文がある（例「スタックちゃん、今日の天気は」）→ 本文を Grok へ。
   - ウェイクワードだけ（例「スタックちゃん」）→ ロボットに `WAKE_ACK_TEXT`（既定「はい？」）を言わせ、`WAKE_WINDOW_SECONDS`（既定 8 秒）のあいだ次の発話を本文として扱う。
   - ウェイクワードが無く、待ち受け中でもない → **捨てる。** Grok には送らない。ログにも本文を残さない。
4. **Grok**: s3 と同じ Webhook。返答は一文にクランプ。
5. **合成と送出**: VOICEVOX で WAV にし、WebSocket で送る。
6. **読み上げ（受信箱）**: `INBOX_POLL_SECONDS`（既定 5 秒）おきに Worker の `GET /messages` を巡回し、届いたテキストを VOICEVOX で合成して送る。鳴らし終えて `played` が返ったら `POST /messages/{id}/ack`。ロボットが未接続なら取り出さず、次回に回す。`INBOX_MESSAGE_TTL_SECONDS`（既定 6 時間）を過ぎたものは読み上げずに ack する。

会話中（`thinking` / `speaking`）に受信箱のメッセージが来たら、会話の一往復が終わってから読み上げます。

## Cloudflare Worker 受信箱

- Worker 名: `stackchan-inbox`。データは D1（`stackchan-inbox`）の `messages` テーブル。
- 認証はヘッダー `Authorization: Bearer <キー>`。**送信キー**（`INBOX_SEND_KEY`、Grok に渡す）と**巡回キー**（`INBOX_POLL_KEY`、Mac に置く）は別物で、送信キーでは読めません。

| メソッドとパス | 誰が | 内容 |
| --- | --- | --- |
| `POST /messages` | Grok routine（送信キー） | `{"text":"…"}`。500 文字まで。応答 `{"id":n}` |
| `GET /messages?after=<id>&limit=20` | Mac（巡回キー） | 未読を古い順に返す `{"messages":[{"id","text","created_at"}]}` |
| `POST /messages/{id}/ack` | Mac（巡回キー） | 読み上げ済みにする |
| `GET /healthz` | 誰でも | `ok` |

Worker はテキストしか扱いません。音声は通りません。本文は制御文字を除き、長さを制限して保存します。

Grok routine 側には「スタックちゃんに読み上げさせたいときは、この URL に `{"text":"…"}` を送信キー付きで POST する」という手順を持たせます。文面は [`../worker/GROK_ROUTINE.md`](../worker/GROK_ROUTINE.md) にあります。

## 認証情報の所在（s4）

| 認証情報 | 置き場所 | ロボット上 |
| --- | --- | --- |
| Grok Webhook URL・sender key | Mac の `.env` | なし |
| 受信箱の巡回キー | Mac の `.env` | なし |
| 受信箱の送信キー | Cloudflare Worker の secret と Grok routine | なし |
| Cloudflare のアカウント認証 | Worker を配置する PC の wrangler | なし |
| Wi-Fi SSID・パスワード、Mac の LAN アドレス | ロボットの `config.h` | あり |

## Mac 上のサービス

`mac/setup.sh` が次を用意し、launchd のユーザーエージェントとして常駐させます。ログイン不要で起動するよう `LaunchAgents` に置き、Mac の自動ログインまたは常時ログイン状態を前提にします。

| ラベル | 内容 | 待受 |
| --- | --- | --- |
| `jp.stackchan.whisper` | `whisper-server`（Homebrew の whisper-cpp、Metal）、日本語、モデルは `WHISPER_MODEL`（既定 `ggml-small.bin`） | `127.0.0.1:8080` |
| `jp.stackchan.voicevox` | VOICEVOX ENGINE macOS arm64 版 | `127.0.0.1:50021` |
| `jp.stackchan.relay` | このリポジトリのリレー（`python3 -m stackchan_grok_relay`） | `<Mac の LAN IPv4>:8787` |

whisper と VOICEVOX はループバックだけで待ち受け、LAN からは見えません。LAN に見えるのはリレーの 8787 だけで、そこに接続してくるのはロボットだけです。macOS のファイアウォールを有効にしている場合は `python3` の受信を許可します。

## 環境変数（`.env`、追加分）

| 変数 | 既定 | 内容 |
| --- | --- | --- |
| `WAKE_WORDS` | `スタックちゃん,すたっくちゃん,スタックチャン` | カンマ区切り。正規化後に前方一致 |
| `WAKE_ACK_TEXT` | `はい？` | ウェイクワードだけのときの返事 |
| `WAKE_WINDOW_SECONDS` | `8` | 返事の後、本文を待つ秒数 |
| `VAD_THRESHOLD` | `600` | 発話とみなす RMS（16 bit） |
| `VAD_SILENCE_MS` | `700` | 発話終了とみなす無音の長さ |
| `VAD_MAX_SECONDS` | `12` | 一発話の上限 |
| `INBOX_URL` | （空。空なら巡回しない） | Worker の URL |
| `INBOX_POLL_KEY` | （空） | 巡回キー |
| `INBOX_POLL_SECONDS` | `5` | 巡回間隔 |
| `INBOX_MESSAGE_TTL_SECONDS` | `21600` | これより古い未読は読み上げない |

`RELAY_HOST` は Mac の LAN IPv4 にします。既存の検査（プライベートアドレスのみ）はそのままです。

## ファームウェアの状態遷移

| 状態 | 顔 | マイク | 入り方 |
| --- | --- | --- | --- |
| `connecting` | 目を閉じる | 送らない | 起動時、切断時 |
| `listening` | 通常 | 送る | 接続直後、`played` 送信後 |
| `awake` | 目を開き、吹き出し「はい？」 | 送る | Mac から `state: awake` |
| `thinking` | 困り顔 | 送る（Mac 側が無視） | Mac から `state: thinking` |
| `speaking` | 口を音量に合わせて動かす | 送らない | `speak` 受信から `played` 送信まで |

## 所有者が行う必要があること

1. **Mac**: システム設定 → 一般 → 共有 → リモートログインを有効にし、この PC の公開鍵を `~/.ssh/authorized_keys` に加える。Mac のホスト名（または LAN IPv4）とユーザー名を伝える。
2. **Cloudflare**: この PC で `npx wrangler login` を一度実行する（ブラウザで許可）。以後は `worker/deploy.sh` が配置する。
3. **Grok**: 会話用の Webhook routine（s3 と同じ）の URL と sender key。読み上げ用に、家族チャットの routine へ `worker/GROK_ROUTINE.md` の手順を追加する。

## 次フェーズ: GPT-Live の試験（2026-09-11 の決定）

所有者の決定: **まず従来方式（VAD → whisper.cpp → Grok → VOICEVOX）で構築し、次のフェーズで OpenAI の GPT-Live-1（`v1/live/sessions`、全二重音声、1 分 0.05 ドル）を費用感を含めて試す。** マイク音声を OpenAI へ送ること、声が OpenAI の声になることは許容する。費用は**月 2,000 円以内**に抑え、従来方式と併用する。

想定の使い方は 1 日 5〜6 回の会話です。1 回を 1.5 分とすると 1 日約 9 分、月約 270 分で **約 13.5 ドル（約 2,000 円）**。上限内に収めるには、次の二つが必須です。

- セッションは呼びかけ（ウェイクワードまたはボタン A）で開き、無言が `LIVE_IDLE_SECONDS`（例 20 秒）続いたら閉じる。常時接続はしない。
- 1 日の合計接続時間に上限（例 15 分）を置き、超えたら従来方式へ自動で切り替える。

### 差し替え位置（拡張性のために決めておくこと）

| 部品 | 従来方式 | GPT-Live 方式 | 共通 |
| --- | --- | --- | --- |
| 発話の区切りと文字起こし | `SpeechSegmenter` + `SpeechToText` | GPT-Live が音声のまま扱う | ウェイクワード判定はどちらも Mac 側で行い、セッションの開閉に使う |
| 応答の生成 | `RelayService` → Grok Webhook | GPT-Live の client delegation から同じ `RelayService` を呼ぶ | Grok Webhook と `prompt.txt` |
| 音声合成 | `TextToSpeech`（VOICEVOX） | GPT-Live の声 | 受信箱の読み上げは当面 VOICEVOX のまま |
| ロボットへの送出 | `speak`（完成した WAV） | `speak_stream`（PCM を流しながら鳴らす。**ファームウェア拡張が必要**） | `state` と `played` |

実装するときは `DeviceSession` の「区切り → 文字起こし → 返答 → 合成 → 送出」を一つの `ConversationEngine` 境界に切り出し、`.env` の `CONVERSATION_ENGINE=pipeline|gpt_live` で選べるようにします。GPT-Live 用の鍵（`OPENAI_API_KEY`）は Mac の `.env` にだけ置きます。ロボットは半二重のまま（再生中はマイクを送らない）で始め、割り込み対応はエコーキャンセルの目処が立ってから検討します。

## 検証の段階

1. `./scripts/test-all.sh`（この PC でも Mac でも）: VAD・ウェイクワード判定・WebSocket 枠組み・受信箱クライアント・ストリーム端点の単体テストと結合検証。2026-09-10 に Windows 上で通過（`tests/test_backup_script.py` の 8 件は Windows 固有の理由で除外）。
2. `python3 -m stackchan_grok_relay.stream_sim --relay-url ws://<Mac>:8787/device/stream --wav <16 kHz モノラル WAV>`: 音声ファイルを WebSocket で流し、`SPOKEN: …` が出ることを確認。`./scripts/test-integration.sh` はこれをモック STT・TTS で自動実行します。
3. Mac で whisper と VOICEVOX を起動し、同じシミュレーターを実音声で通す。
4. 実機を新ファームウェアにして、「スタックちゃん」→「はい？」→ 質問 → 返答、を確認。
5. Worker に `curl` で投函し、ロボットが読み上げることを確認。
