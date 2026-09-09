#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PATTERN='(xai-[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|Bearer [A-Za-z0-9._-]{24,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)'
mapfile -d '' FILES < <(git ls-files -z --cached --others --exclude-standard | while IFS= read -r -d '' file; do
	if [[ "$file" != ".env.example" ]]; then
		printf '%s\0' "$file"
	fi
done)

if ((${#FILES[@]} > 0)); then
	FINDINGS="$(grep -EnH --binary-files=without-match -e "$PATTERN" -- "${FILES[@]}" || true)"
else
	FINDINGS=""
fi

if [[ -n "$FINDINGS" ]]; then
	echo "シークレット候補を検出しました。" >&2
	printf '%s\n' "$FINDINGS" >&2
	exit 1
fi

echo "シークレット検査の検出件数は 0 件です。"
