# 常時起動 Mac の準備（s4）

リレー・STT（whisper.cpp）・TTS（VOICEVOX ENGINE）を常時起動の Mac（Apple Silicon）に置き、launchd で常駐させます。設計は [`../docs/always-on-mac.md`](../docs/always-on-mac.md) にあります。

| ファイル | どこで実行 | 内容 |
| --- | --- | --- |
| `mac/deploy.sh` | この PC（Git Bash） | リポジトリを Mac へ送り、リレーを再起動する。`--setup` で下の setup.sh も実行 |
| `mac/setup.sh` | Mac | Homebrew パッケージ、whisper モデル、VOICEVOX ENGINE を用意し、launchd に三つのサービスを登録する |

## 前提（所有者が Mac で一度だけ）

1. **リモートログイン**: システム設定 → 一般 → 共有 → リモートログインをオン。
2. **公開鍵**: この PC の公開鍵（`~/.ssh/id_ed25519.pub` など）を Mac の `~/.ssh/authorized_keys` に追加。`ssh user@mac 'echo ok'` がパスワード無しで通ること。
3. **Homebrew**: https://brew.sh の手順で導入済みであること（`brew --version` が通る）。
4. **ログイン状態**: サービスはユーザーの launchd（`gui/<uid>`）に登録するため、Mac はそのユーザーでログインしたままにします（自動ログインを推奨）。スリープしない設定にします（システム設定 → エネルギー → 「ディスプレイがオフのときに自動でスリープさせない」）。

## 手順（二つのコマンド）

```bash
# 1. この PC で。初回は --setup を付ける（数 GB のダウンロードがあるので時間がかかります）
mac/deploy.sh --host user@mac-mini.local --setup

# 2. Mac で .env を編集する（setup.sh が .env.example から作った .env が置かれています）
ssh user@mac-mini.local
nano ~/stackchan/app/.env
```

`.env` で最低限見るのは次です。

| 変数 | 値 |
| --- | --- |
| `RELAY_HOST` | この Mac の LAN IPv4。ロボットの `config.h` と同じ値。取得は `ipconfig getifaddr "$(route -n get default | awk '/interface:/{print $2}')"`（有線なら en1 のことがあり、`en0` 決め打ちでは空が返ります） |
| `GROK_WEBHOOK_URL` / `GROK_WEBHOOK_SENDER_KEY` | 会話用 routine の URL と sender key |
| `STT_ENGINE=whisper_http` / `STT_URL=http://127.0.0.1:8080/inference` | |
| `TTS_ENGINE=voicevox_http` / `TTS_URL=http://127.0.0.1:50021` | |
| `INBOX_URL` / `INBOX_POLL_KEY` | Worker の URL と巡回キー（[`../worker/README.md`](../worker/README.md)） |

編集したらリレーを再起動します（`mac/deploy.sh` を再実行しても同じことをします）。

```bash
launchctl kickstart -k gui/$(id -u)/jp.stackchan.relay
```

二回目以降の更新は `mac/deploy.sh --host user@mac-mini.local` だけです。`.env` は上書きされません。

## 状態の確認

```bash
launchctl list | grep jp.stackchan          # 三つ並び、PID が付いていれば動作中
tail -f ~/stackchan/logs/relay.log
tail -f ~/stackchan/logs/whisper.log
tail -f ~/stackchan/logs/voicevox.log
curl -sS http://127.0.0.1:50021/version     # VOICEVOX
curl -sS http://127.0.0.1:8080/health       # whisper-server
```

置き場所は次のとおりです。

| パス | 内容 |
| --- | --- |
| `~/stackchan/app` | リポジトリ（`--repo` で変更可） |
| `~/stackchan/models/ggml-small.bin` | whisper モデル（`WHISPER_MODEL=medium bash mac/setup.sh` で差し替え） |
| `~/stackchan/voicevox/` | VOICEVOX ENGINE（`run` 実行ファイル） |
| `~/stackchan/logs/` | 三つのログ |
| `~/Library/LaunchAgents/jp.stackchan.*.plist` | launchd の定義 |

whisper と VOICEVOX は `127.0.0.1` だけで待ち受けます。LAN から見えるのはリレーの `RELAY_HOST:8787` だけです。

## ファイアウォールで python3 を許可する

macOS のファイアウォール（システム設定 → ネットワーク → ファイアウォール）を有効にしていると、リレーの受信（8787）が止められます。次のどちらかで許可します。

- GUI: 初回起動時に「python3 が着信接続を受け入れることを許可しますか」と出たら「許可」。出なかったら、ファイアウォールの「オプション…」で `+` を押し、`/opt/homebrew/bin/python3`（Homebrew の python3 を使っている場合。`which python3` で確認）を追加して「着信接続を許可」にする。
- コマンド:

  ```bash
  PY="$(readlink -f "$(which python3)")"
  sudo /usr/libexec/ApplicationFirewall/socketfilterfw --add "$PY"
  sudo /usr/libexec/ApplicationFirewall/socketfilterfw --unblockapp "$PY"
  ```

whisper と VOICEVOX はループバックだけなので許可は要りません。

## 止める・外す

```bash
# 一時停止（再起動やログインで戻ります）
launchctl bootout gui/$(id -u)/jp.stackchan.relay
launchctl bootout gui/$(id -u)/jp.stackchan.whisper
launchctl bootout gui/$(id -u)/jp.stackchan.voicevox

# 完全に外す
launchctl bootout gui/$(id -u)/jp.stackchan.relay gui/$(id -u)/jp.stackchan.whisper gui/$(id -u)/jp.stackchan.voicevox 2>/dev/null
rm ~/Library/LaunchAgents/jp.stackchan.*.plist
rm -rf ~/stackchan        # モデル・VOICEVOX・ログ・リポジトリ（.env を含む）も消えます
```

再び動かすときは `mac/deploy.sh --host user@mac --setup` を実行します。

## うまくいかないとき

- `Bootstrap failed` / `Domain does not support specified action`: ユーザーの GUI セッションがありません。Mac にログインしてから再実行します。
- VOICEVOX が 60 秒で応答しない: 初回は起動に時間がかかります。`voicevox.log` を見て待ちます。「開発元を確認できない」で止まる場合は `xattr -dr com.apple.quarantine ~/stackchan/voicevox` を実行します。
- ロボットから繋がらない: `.env` の `RELAY_HOST` が Mac の現在の LAN IPv4 か、ファイアウォールで python3 を許可しているか、を確認します。ルーターで Mac の IP を固定しておくと安定します。
