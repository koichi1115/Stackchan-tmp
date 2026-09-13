-- stackchan-inbox の messages テーブル。
-- リモートの D1 には同じ定義が既にあります。このファイルはローカル開発（wrangler dev）と記録のためのものです。
-- 何度実行しても同じ結果になります。

CREATE TABLE IF NOT EXISTS messages (
	id INTEGER PRIMARY KEY AUTOINCREMENT,
	text TEXT NOT NULL,
	created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
	delivered_at TEXT,
	reply_to TEXT
);

CREATE INDEX IF NOT EXISTS idx_messages_undelivered ON messages (delivered_at, id);
CREATE INDEX IF NOT EXISTS idx_messages_reply_to ON messages (reply_to, delivered_at, id);

-- 既存のテーブルに reply_to 列が無い場合は migrations/0002_reply_to.sql を適用します
-- （deploy.sh が行います。列が既にあるときの失敗は無視して構いません）。
