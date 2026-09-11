-- stackchan-inbox の messages テーブル。
-- リモートの D1 には同じ定義が既にあります。このファイルはローカル開発（wrangler dev）と記録のためのものです。
-- 何度実行しても同じ結果になります。

CREATE TABLE IF NOT EXISTS messages (
	id INTEGER PRIMARY KEY AUTOINCREMENT,
	text TEXT NOT NULL,
	created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
	delivered_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_messages_undelivered ON messages (delivered_at, id);
