#!/usr/bin/env bash
set -euo pipefail

# Cloudflare Worker「stackchan-inbox」を配置します。
#
#   ./deploy.sh          D1 にスキーマを適用 → Worker を配置 → secret の設定状況を確認
#   ./deploy.sh --check  ログイン状態の確認だけ（wrangler whoami）
#
# 前提: この PC で `npx --yes wrangler@4 login` を一度済ませていること（ブラウザで許可）。
# 何度実行しても同じ結果になります。secret はここでは設定しません（末尾の案内に従って手で入れます）。

WORKER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$WORKER_DIR"

WRANGLER=(npx --yes wrangler@4)
DB_NAME="stackchan-inbox"

usage() {
	cat <<USAGE
使い方: $(basename "$0") [--check]

  (引数なし)  D1 ($DB_NAME) に schema.sql を適用し、Worker を配置し、secret の設定状況を表示します
  --check     wrangler whoami だけ実行し、Cloudflare にログインできているか確かめます
USAGE
}

CHECK_ONLY=0
while (($# > 0)); do
	case "$1" in
		--check) CHECK_ONLY=1 ;;
		-h|--help) usage; exit 0 ;;
		*)
			echo "不明な引数: $1" >&2
			usage >&2
			exit 2
			;;
	esac
	shift
done

if ((CHECK_ONLY)); then
	"${WRANGLER[@]}" whoami
	exit 0
fi

echo "== D1 ($DB_NAME) にスキーマを適用します（CREATE IF NOT EXISTS のみ。既存の行には触れません）"
"${WRANGLER[@]}" d1 execute "$DB_NAME" --remote --yes --file schema.sql
# 既存テーブルに reply_to 列を足す。既にあれば "duplicate column name" で失敗するので、それだけは無視する。
if ! MIGRATION_OUTPUT="$("${WRANGLER[@]}" d1 execute "$DB_NAME" --remote --yes --file migrations/0002_reply_to.sql 2>&1)"; then
	if printf '%s' "$MIGRATION_OUTPUT" | grep -qi "duplicate column"; then
		echo "reply_to 列は既にあります。"
	else
		printf '%s\n' "$MIGRATION_OUTPUT" >&2
		exit 1
	fi
fi

echo "== Worker を配置します"
"${WRANGLER[@]}" deploy

echo "== secret の設定状況を確認します"
SECRET_LIST="$("${WRANGLER[@]}" secret list 2>/dev/null || true)"
MISSING=()
for name in INBOX_SEND_KEY INBOX_POLL_KEY; do
	if grep -Eq "\"name\"[[:space:]]*:[[:space:]]*\"$name\"" <<<"$SECRET_LIST"; then
		echo "  $name: 設定済み"
	else
		echo "  $name: 未設定"
		MISSING+=("$name")
	fi
done

if ((${#MISSING[@]} > 0)); then
	cat <<GUIDE

secret が未設定のあいだ、Worker は全経路に 503 を返します（設定途中で開いたままにしません）。
次の手順で設定してください。キーは二つとも別々に生成します。

  1. キーを生成する（一つにつき一回）:
       python3 -c "import secrets; print(secrets.token_urlsafe(32))"

  2. 生成した値を貼り付けて secret に入れる（プロンプトが出ます）:
GUIDE
	for name in "${MISSING[@]}"; do
		echo "       (cd \"$WORKER_DIR\" && npx --yes wrangler@4 secret put $name)"
	done
	cat <<GUIDE

  3. INBOX_SEND_KEY は Grok routine に渡します（GROK_ROUTINE.md）。
     INBOX_POLL_KEY は Mac の .env の INBOX_POLL_KEY に置きます。同じ .env の INBOX_URL には
     上の deploy が表示した https://stackchan-inbox.<account>.workers.dev を入れます。
GUIDE
else
	cat <<GUIDE

secret は両方設定済みです。キーを入れ替えるときは次を実行します（値は python3 -c "import secrets; print(secrets.token_urlsafe(32))" で生成）:
  (cd "$WORKER_DIR" && npx --yes wrangler@4 secret put INBOX_SEND_KEY)
  (cd "$WORKER_DIR" && npx --yes wrangler@4 secret put INBOX_POLL_KEY)
GUIDE
fi

echo
echo "動作確認: curl -sS https://stackchan-inbox.<account>.workers.dev/healthz  → ok"
