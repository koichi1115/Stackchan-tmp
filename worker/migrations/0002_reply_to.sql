-- 会話の返事に付く照合 ID。既存テーブルへ列を足す。列が既にあると失敗するが、それは無視してよい。
ALTER TABLE messages ADD COLUMN reply_to TEXT;
CREATE INDEX IF NOT EXISTS idx_messages_reply_to ON messages (reply_to, delivered_at, id);
