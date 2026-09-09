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

# firmware/ には Wi-Fi 資格情報とリレーのアドレス以外を置きません。
FIRMWARE_FINDINGS=""

if git ls-files --error-unmatch firmware/include/config.h >/dev/null 2>&1; then
	FIRMWARE_FINDINGS+="firmware/include/config.h が Git 管理下にあります。"$'\n'
fi

mapfile -d '' FIRMWARE_FILES < <(git ls-files -z --cached --others --exclude-standard -- firmware)
if ((${#FIRMWARE_FILES[@]} > 0)); then
	KEY_PATTERN='#[[:space:]]*define[[:space:]]+[A-Za-z0-9_]*(API_KEY|APIKEY|TOKEN|SECRET|SENDER_KEY|WEBHOOK|CREDENTIAL|PRIVATE_KEY)'
	FIRMWARE_FINDINGS+="$(grep -EnH --binary-files=without-match -e "$KEY_PATTERN" -- "${FIRMWARE_FILES[@]}" || true)"

	PLACEHOLDER_MISMATCH="$(
		grep -EnH --binary-files=without-match -e '#[[:space:]]*define[[:space:]]+WIFI_PASSWORD' -- "${FIRMWARE_FILES[@]}" |
			grep -Fv '"your-wifi-password"' || true
	)"
	if [[ -n "$PLACEHOLDER_MISMATCH" ]]; then
		FIRMWARE_FINDINGS+=$'\n'"$PLACEHOLDER_MISMATCH"
	fi
fi

FIRMWARE_FINDINGS="$(printf '%s' "$FIRMWARE_FINDINGS" | sed '/^$/d')"
if [[ -n "$FIRMWARE_FINDINGS" ]]; then
	echo "firmware/ にシークレット候補を検出しました。" >&2
	printf '%s\n' "$FIRMWARE_FINDINGS" >&2
	exit 1
fi

echo "シークレット検査の検出件数は 0 件です（firmware/ を含みます）。"
