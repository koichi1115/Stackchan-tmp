#!/usr/bin/env bash
set -euo pipefail

# この PC（Git Bash）から常時起動 Mac へリポジトリを送り、リレーを再起動します。
#
#   mac/deploy.sh --host <user@host> [--repo ~/stackchan/app] [--setup]
#
#   --host <user@host>  Mac の SSH 接続先（例 sai@mac-mini.local）
#   --repo <path>       Mac 上の置き場所（既定 ~/stackchan/app）
#   --setup             送った後に Mac 上で mac/setup.sh を実行する（初回、または構成を変えたとき）
#
# 送るのは Git が管理する内容（未追跡ファイルも含み、.gitignore 対象は除く）です。
# Windows で改行が CRLF になっていても、LF に揃えて送ります。
# .env は決して送りません（Mac 側の .env を上書きしません）。.env.example だけ送ります。

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST=""
REPO='~/stackchan/app'
RUN_SETUP=0
RELAY_LABEL="jp.stackchan.relay"

# 送らないもの（rsync の --delete からも守る）
EXCLUDES=(
	.git
	.pio
	backups
	firmware/.pio
	firmware/include/config.h
	__pycache__
	.env
	.venv
	.wrangler
	.dev.vars
	node_modules
)

usage() {
	cat <<USAGE
使い方: mac/deploy.sh --host <user@host> [--repo <path>] [--setup]

  --host <user@host>  Mac の SSH 接続先（必須。例 sai@mac-mini.local）
  --repo <path>       Mac 上の置き場所（既定 ~/stackchan/app）
  --setup             送った後に Mac 上で mac/setup.sh を実行する

送った後は必ず jp.stackchan.relay を再起動し、~/stackchan/logs/relay.log の末尾 20 行を表示します。
USAGE
}

die() {
	printf 'エラー: %s\n' "$*" >&2
	exit 1
}

log() {
	printf '== %s\n' "$*"
}

while (($# > 0)); do
	case "$1" in
		--host)
			[[ $# -ge 2 ]] || die "--host には user@host が要ります"
			HOST="$2"
			shift
			;;
		--repo)
			[[ $# -ge 2 ]] || die "--repo にはパスが要ります"
			REPO="$2"
			shift
			;;
		--setup) RUN_SETUP=1 ;;
		-h|--help) usage; exit 0 ;;
		*)
			usage >&2
			die "不明な引数: $1"
			;;
	esac
	shift
done

if [[ -z "$HOST" ]]; then
	usage >&2
	die "--host を指定してください"
fi

for tool in ssh git tar; do
	command -v "$tool" >/dev/null 2>&1 || die "$tool が見つかりません"
done
git -C "$ROOT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "$ROOT_DIR は Git リポジトリではありません"

# Git for Windows に渡すパスは D:/... 形式にする（cygpath が無い環境ではそのまま）
native_path() {
	if command -v cygpath >/dev/null 2>&1; then
		cygpath -m "$1"
	else
		printf '%s' "$1"
	fi
}

# --- 送る内容を一時ディレクトリに書き出す ---------------------------------------------------
#
# 一時的な index に `git add -A` して（未追跡も拾い、.gitignore は除き、CRLF→LF を正規化）、
# それを checkout-index で書き出す。本物の index と作業ツリーには触れない。

STAGE="$(mktemp -d "${TMPDIR:-/tmp}/stackchan-deploy.XXXXXX")"
TMP_INDEX="$STAGE.index"
trap 'rm -rf "$STAGE" "$TMP_INDEX"' EXIT

log "送る内容を書き出します（LF 改行に揃えます）"
(
	cd "$ROOT_DIR"
	export GIT_INDEX_FILE
	GIT_INDEX_FILE="$(native_path "$TMP_INDEX")"
	git -c core.safecrlf=false add -A -- .
	git -c core.autocrlf=false -c core.eol=lf checkout-index -a -f --prefix="$(native_path "$STAGE")/"
)

# .gitignore で除かれているはずだが、念のため
for pattern in "${EXCLUDES[@]}"; do
	rm -rf "${STAGE:?}/$pattern"
done
find "$STAGE" -type d \( -name __pycache__ -o -name .pio -o -name .wrangler \) -prune -exec rm -rf {} + 2>/dev/null || true
find "$STAGE" \( -name .env -o -name .dev.vars -o -name config.h -path '*/firmware/include/*' \) -type f -delete 2>/dev/null || true
[[ ! -e "$STAGE/.env" ]] || die ".env が送る内容に混ざっています。中止します。"
find "$STAGE" -name '*.sh' -type f -exec chmod +x {} + 2>/dev/null || true

# --- Mac 側のパスを決める ---------------------------------------------------------------------

log "$HOST に接続します"
REMOTE_HOME="$(ssh "$HOST" 'printf %s "$HOME"')"
[[ -n "$REMOTE_HOME" ]] || die "Mac の HOME を取得できませんでした"
case "$REPO" in
	"~") REPO_ABS="$REMOTE_HOME" ;;
	"~/"*) REPO_ABS="$REMOTE_HOME/${REPO#"~/"}" ;;
	/*) REPO_ABS="$REPO" ;;
	*) REPO_ABS="$REMOTE_HOME/$REPO" ;;
esac
log "送り先: $HOST:$REPO_ABS"

# --- 送る -------------------------------------------------------------------------------------

use_rsync=0
if command -v rsync >/dev/null 2>&1 && ssh "$HOST" 'command -v rsync >/dev/null 2>&1'; then
	use_rsync=1
fi

if ((use_rsync)); then
	log "rsync で送ります"
	RSYNC_EXCLUDES=()
	for pattern in "${EXCLUDES[@]}"; do
		RSYNC_EXCLUDES+=(--exclude "$pattern")
	done
	ssh "$HOST" "mkdir -p '$REPO_ABS'"
	rsync -az --delete "${RSYNC_EXCLUDES[@]}" "$STAGE/" "$HOST:$REPO_ABS/"
else
	log "rsync が無いので tar で送ります（Mac 側で消えたファイルは残ります）"
	tar czf - -C "$STAGE" . | ssh "$HOST" "mkdir -p '$REPO_ABS' && tar xzf - -C '$REPO_ABS'"
fi
ssh "$HOST" "find '$REPO_ABS' -name '*.sh' -type f -exec chmod +x {} +"

# --- Mac 側の作業 --------------------------------------------------------------------------------

STATUS=0
if ((RUN_SETUP)); then
	log "Mac 上で mac/setup.sh を実行します"
	ssh "$HOST" "bash '$REPO_ABS/mac/setup.sh' --repo '$REPO_ABS'" || {
		STATUS=$?
		printf '注意: setup.sh が終了コード %s で終わりました。上の出力を確認してください。\n' "$STATUS" >&2
	}
fi

log "リレーを再起動します ($RELAY_LABEL)"
if ! ssh "$HOST" 'launchctl kickstart -k "gui/$(id -u)/'"$RELAY_LABEL"'"' 2>/dev/null; then
	echo "（$RELAY_LABEL はまだ登録されていません。初回は --setup を付けて実行してください）"
fi

log "relay.log の末尾"
sleep 2
ssh "$HOST" 'tail -n 20 ~/stackchan/logs/relay.log 2>/dev/null || echo "relay.log はまだありません"'

exit "$STATUS"
