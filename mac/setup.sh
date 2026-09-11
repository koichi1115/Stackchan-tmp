#!/usr/bin/env bash
set -euo pipefail

# 常時起動 Mac（Apple Silicon）に whisper.cpp・VOICEVOX ENGINE・リレーを常駐させます。
# この Mac 上で実行します（通常は mac/deploy.sh --setup が ssh 越しに呼びます）。
# 何度実行しても同じ状態になります。
#
#   bash mac/setup.sh [--repo <path>] [--skip-services]
#
#   --repo <path>      リレーのリポジトリの置き場所（既定 ~/stackchan/app）
#   --skip-services    パッケージ導入とダウンロードだけ行い、launchd への登録と起動確認をしない
#   WHISPER_MODEL=…    whisper のモデル名（既定 small → ggml-small.bin）
#
# macOS 標準の bash 3.2 で動くように書いています（mapfile や連想配列は使いません）。

# ssh 越しの非ログインシェルでは Homebrew が PATH に無いことがある
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:$PATH"

BASE_DIR="$HOME/stackchan"
MODELS_DIR="$BASE_DIR/models"
VOICEVOX_DIR="$BASE_DIR/voicevox"
LOGS_DIR="$BASE_DIR/logs"
AGENTS_DIR="$HOME/Library/LaunchAgents"
REPO_DIR="$HOME/stackchan/app"
SKIP_SERVICES=0
WHISPER_MODEL="${WHISPER_MODEL:-small}"
WHISPER_PORT=8080
VOICEVOX_PORT=50021
WHISPER_MODEL_URL_BASE="https://huggingface.co/ggerganov/whisper.cpp/resolve/main"
VOICEVOX_RELEASE_API="https://api.github.com/repos/VOICEVOX/voicevox_engine/releases/latest"
# 実際の資産名は voicevox_engine-macos-arm64-<version>.7z.001（複数分割なら .002 …）。
# 将来 -cpu が挟まっても拾えるようにしておく。
VOICEVOX_ASSET_PATTERN='^voicevox_engine-macos-arm64(-cpu)?-[0-9][^/]*[.]7z[.][0-9]{3}$'

log() {
	printf '== %s\n' "$*"
}

warn() {
	printf '注意: %s\n' "$*" >&2
}

die() {
	printf 'エラー: %s\n' "$*" >&2
	exit 1
}

usage() {
	cat <<USAGE
使い方: bash mac/setup.sh [--repo <path>] [--skip-services]

  --repo <path>     リレーのリポジトリの置き場所（既定 ~/stackchan/app）
  --skip-services   パッケージ導入とダウンロードだけ行い、launchd への登録をしない
  WHISPER_MODEL=…   whisper のモデル名（既定 small）
USAGE
}

expand_home() {
	case "$1" in
		"~") printf '%s' "$HOME" ;;
		"~/"*) printf '%s/%s' "$HOME" "${1#"~/"}" ;;
		*) printf '%s' "$1" ;;
	esac
}

while (($# > 0)); do
	case "$1" in
		--repo)
			[[ $# -ge 2 ]] || die "--repo にはパスが要ります"
			REPO_DIR="$(expand_home "$2")"
			shift
			;;
		--skip-services) SKIP_SERVICES=1 ;;
		-h|--help) usage; exit 0 ;;
		*)
			usage >&2
			die "不明な引数: $1"
			;;
	esac
	shift
done

[[ "$(uname -s)" == "Darwin" ]] || die "macOS 専用です（$(uname -s) で実行されました）。"
[[ "$(uname -m)" == "arm64" ]] || die "Apple Silicon (arm64) 専用です（$(uname -m)）。VOICEVOX の配布物が macOS arm64 版のためです。"
command -v brew >/dev/null 2>&1 || die "Homebrew が見つかりません。https://brew.sh の手順で導入してから再実行してください。"
command -v python3 >/dev/null 2>&1 || die "python3 が見つかりません。"
command -v curl >/dev/null 2>&1 || die "curl が見つかりません。"

mkdir -p "$MODELS_DIR" "$VOICEVOX_DIR" "$LOGS_DIR" "$AGENTS_DIR"

# --- Homebrew --------------------------------------------------------------------

brew_install_if_missing() {
	if brew list --formula "$1" >/dev/null 2>&1; then
		log "$1 は導入済みです"
	else
		log "$1 を導入します"
		brew install "$1"
	fi
}

brew_install_if_missing whisper-cpp
brew_install_if_missing sevenzip

WHISPER_PREFIX="$(brew --prefix whisper-cpp)"
WHISPER_SERVER="$WHISPER_PREFIX/bin/whisper-server"
[[ -x "$WHISPER_SERVER" ]] || die "whisper-server が見つかりません: ${WHISPER_SERVER}（whisper-cpp の版によっては同梱されないことがあります。brew info whisper-cpp を確認してください）"

SEVENZIP="$(command -v 7zz || true)"
[[ -n "$SEVENZIP" ]] || SEVENZIP="$(brew --prefix sevenzip)/bin/7zz"
[[ -x "$SEVENZIP" ]] || die "7zz が見つかりません。"

# --- whisper モデル ---------------------------------------------------------------

MODEL_FILE="$MODELS_DIR/ggml-${WHISPER_MODEL}.bin"
if [[ -s "$MODEL_FILE" ]]; then
	log "whisper モデルはあります: $MODEL_FILE"
else
	log "whisper モデルを取得します: ggml-${WHISPER_MODEL}.bin"
	curl -fL --retry 3 --progress-bar -o "$MODEL_FILE.part" "$WHISPER_MODEL_URL_BASE/ggml-${WHISPER_MODEL}.bin"
	mv "$MODEL_FILE.part" "$MODEL_FILE"
fi

# --- VOICEVOX ENGINE ---------------------------------------------------------------

find_voicevox_run() {
	find "$VOICEVOX_DIR" -maxdepth 4 -type f -name run 2>/dev/null | head -n 1
}

file_size() {
	stat -f %z "$1" 2>/dev/null || echo 0
}

VOICEVOX_RUN="$(find_voicevox_run)"
if [[ -n "$VOICEVOX_RUN" ]]; then
	log "VOICEVOX ENGINE はあります: $VOICEVOX_RUN"
else
	log "VOICEVOX ENGINE の最新版（macOS arm64）を調べます"
	# 1 行に「ファイル名 サイズ URL」。分割は .001 .002 … の順に並ぶ。
	ASSET_LINES="$(curl -fsSL "$VOICEVOX_RELEASE_API" | VOICEVOX_ASSET_PATTERN="$VOICEVOX_ASSET_PATTERN" python3 -c '
import json, os, re, sys
release = json.load(sys.stdin)
pattern = re.compile(os.environ["VOICEVOX_ASSET_PATTERN"])
assets = sorted((a for a in release.get("assets", []) if pattern.match(a["name"])), key=lambda a: a["name"])
sys.stderr.write("release: " + str(release.get("tag_name", "?")) + chr(10))
for a in assets:
	print(a["name"], a["size"], a["browser_download_url"])
')"
	[[ -n "$ASSET_LINES" ]] || die "macOS arm64 版の資産が見つかりません。$VOICEVOX_RELEASE_API を確認してください。"

	FIRST_PART=""
	PART_FILES=()
	while IFS=' ' read -r name size url; do
		[[ -n "$name" ]] || continue
		target="$VOICEVOX_DIR/$name"
		if [[ -f "$target" && "$(file_size "$target")" == "$size" ]]; then
			log "取得済み: $name"
		else
			log "取得します: $name ($size bytes)"
			curl -fL --retry 3 --progress-bar -o "$target.part" "$url"
			mv "$target.part" "$target"
		fi
		PART_FILES+=("$target")
		[[ -n "$FIRST_PART" ]] || FIRST_PART="$target"
	done <<<"$ASSET_LINES"

	log "展開します（数分かかります）"
	"$SEVENZIP" x -y -o"$VOICEVOX_DIR" "$FIRST_PART" >"$LOGS_DIR/voicevox-extract.log" 2>&1 || die "展開に失敗しました。$LOGS_DIR/voicevox-extract.log を確認してください。"

	VOICEVOX_RUN="$(find_voicevox_run)"
	[[ -n "$VOICEVOX_RUN" ]] || die "展開後に run が見つかりません（${VOICEVOX_DIR}）。"
	chmod +x "$VOICEVOX_RUN"
	xattr -dr com.apple.quarantine "$(dirname "$VOICEVOX_RUN")" 2>/dev/null || true
	rm -f "${PART_FILES[@]}"
	log "VOICEVOX ENGINE を置きました: $VOICEVOX_RUN"
fi
chmod +x "$VOICEVOX_RUN"
VOICEVOX_HOME="$(cd "$(dirname "$VOICEVOX_RUN")" && pwd)"

# --- リポジトリと .env -----------------------------------------------------------------

if [[ ! -f "$REPO_DIR/scripts/run-relay.sh" ]]; then
	warn "$REPO_DIR にリポジトリがありません。先に mac/deploy.sh で送ってください。リレーは登録されても起動に失敗し続けます。"
fi
if [[ ! -f "$REPO_DIR/.env" && -f "$REPO_DIR/.env.example" ]]; then
	cp "$REPO_DIR/.env.example" "$REPO_DIR/.env"
	chmod 600 "$REPO_DIR/.env"
	warn "$REPO_DIR/.env を .env.example から作りました。RELAY_HOST（この Mac の LAN IPv4）、Grok の URL とキー、STT/TTS、INBOX_* を編集してください。"
fi

if ((SKIP_SERVICES)); then
	log "--skip-services のため launchd への登録は行いません。"
	exit 0
fi

# --- launchd --------------------------------------------------------------------------

UID_NUM="$(id -u)"
WHISPER_LABEL="jp.stackchan.whisper"
VOICEVOX_LABEL="jp.stackchan.voicevox"
RELAY_LABEL="jp.stackchan.relay"

xml_escape() {
	printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'
}

WHISPER_ENV=""
METAL_RESOURCES="$WHISPER_PREFIX/share/whisper-cpp"
if [[ -d "$METAL_RESOURCES" ]]; then
	WHISPER_ENV="		<key>GGML_METAL_PATH_RESOURCES</key>
		<string>$(xml_escape "$METAL_RESOURCES")</string>"
fi

# 注: Homebrew の whisper-server (1.9.x) に --no-prints は無いので付けません。
cat >"$AGENTS_DIR/$WHISPER_LABEL.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key>
	<string>$WHISPER_LABEL</string>
	<key>ProgramArguments</key>
	<array>
		<string>$(xml_escape "$WHISPER_SERVER")</string>
		<string>-m</string>
		<string>$(xml_escape "$MODEL_FILE")</string>
		<string>-l</string>
		<string>ja</string>
		<string>--host</string>
		<string>127.0.0.1</string>
		<string>--port</string>
		<string>$WHISPER_PORT</string>
	</array>
	<key>EnvironmentVariables</key>
	<dict>
		<key>PATH</key>
		<string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
$WHISPER_ENV
	</dict>
	<key>WorkingDirectory</key>
	<string>$(xml_escape "$BASE_DIR")</string>
	<key>RunAtLoad</key>
	<true/>
	<key>KeepAlive</key>
	<true/>
	<key>ThrottleInterval</key>
	<integer>5</integer>
	<key>StandardOutPath</key>
	<string>$(xml_escape "$LOGS_DIR/whisper.log")</string>
	<key>StandardErrorPath</key>
	<string>$(xml_escape "$LOGS_DIR/whisper.log")</string>
</dict>
</plist>
PLIST

cat >"$AGENTS_DIR/$VOICEVOX_LABEL.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key>
	<string>$VOICEVOX_LABEL</string>
	<key>ProgramArguments</key>
	<array>
		<string>$(xml_escape "$VOICEVOX_RUN")</string>
		<string>--host</string>
		<string>127.0.0.1</string>
		<string>--port</string>
		<string>$VOICEVOX_PORT</string>
	</array>
	<key>WorkingDirectory</key>
	<string>$(xml_escape "$VOICEVOX_HOME")</string>
	<key>RunAtLoad</key>
	<true/>
	<key>KeepAlive</key>
	<true/>
	<key>ThrottleInterval</key>
	<integer>5</integer>
	<key>StandardOutPath</key>
	<string>$(xml_escape "$LOGS_DIR/voicevox.log")</string>
	<key>StandardErrorPath</key>
	<string>$(xml_escape "$LOGS_DIR/voicevox.log")</string>
</dict>
</plist>
PLIST

cat >"$AGENTS_DIR/$RELAY_LABEL.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key>
	<string>$RELAY_LABEL</string>
	<key>ProgramArguments</key>
	<array>
		<string>/bin/bash</string>
		<string>scripts/run-relay.sh</string>
	</array>
	<key>WorkingDirectory</key>
	<string>$(xml_escape "$REPO_DIR")</string>
	<key>EnvironmentVariables</key>
	<dict>
		<key>PATH</key>
		<string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
		<key>PYTHONUNBUFFERED</key>
		<string>1</string>
	</dict>
	<key>RunAtLoad</key>
	<true/>
	<key>KeepAlive</key>
	<true/>
	<key>ThrottleInterval</key>
	<integer>5</integer>
	<key>StandardOutPath</key>
	<string>$(xml_escape "$LOGS_DIR/relay.log")</string>
	<key>StandardErrorPath</key>
	<string>$(xml_escape "$LOGS_DIR/relay.log")</string>
</dict>
</plist>
PLIST

for label in "$WHISPER_LABEL" "$VOICEVOX_LABEL" "$RELAY_LABEL"; do
	plutil -lint -s "$AGENTS_DIR/$label.plist" >/dev/null || die "$label.plist が不正です。"
done

load_agent() {
	local label="$1"
	local plist="$AGENTS_DIR/$label.plist"
	local attempt
	launchctl bootout "gui/$UID_NUM/$label" >/dev/null 2>&1 || true
	for attempt in 1 2 3 4 5; do
		if launchctl bootstrap "gui/$UID_NUM" "$plist" 2>/dev/null; then
			log "$label を登録しました"
			return 0
		fi
		sleep 1
	done
	warn "$label を登録できませんでした。この Mac にユーザーがログインしている（GUI セッションがある）必要があります。手動: launchctl bootstrap gui/$UID_NUM $plist"
	return 1
}

STATUS=0
load_agent "$WHISPER_LABEL" || STATUS=1
load_agent "$VOICEVOX_LABEL" || STATUS=1
load_agent "$RELAY_LABEL" || STATUS=1

# --- 起動確認 ---------------------------------------------------------------------------

wait_for() {
	local name="$1" url="$2" logfile="$3"
	local deadline=$((SECONDS + 60))
	while ((SECONDS < deadline)); do
		if curl -fsS -m 3 -o /dev/null "$url"; then
			log "$name: 応答あり ($url)"
			return 0
		fi
		sleep 2
	done
	warn "$name: 60 秒待っても応答がありません ($url)。初回は起動に時間がかかることがあります。ログ: $logfile"
	return 1
}

wait_for "VOICEVOX ENGINE" "http://127.0.0.1:$VOICEVOX_PORT/version" "$LOGS_DIR/voicevox.log" || STATUS=1
wait_for "whisper-server" "http://127.0.0.1:$WHISPER_PORT/health" "$LOGS_DIR/whisper.log" || STATUS=1

log "launchd の状態:"
launchctl list 2>/dev/null | grep 'jp[.]stackchan' || warn "jp.stackchan.* が launchctl list に見えません"

if ((STATUS == 0)); then
	log "完了。次は $REPO_DIR/.env を確認し、必要なら launchctl kickstart -k gui/$UID_NUM/$RELAY_LABEL で再起動してください。"
else
	warn "一部が未完了です。上の注意を確認してください。"
fi
exit "$STATUS"
