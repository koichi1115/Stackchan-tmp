# Grok Bot routine への追加手順（スタックちゃんに読み上げさせる）

家族チャットの Grok Bot routine に、次の道具を持たせます。誰かが「スタックちゃんに伝えて」「スタックちゃんに言わせて」などと頼んだら、Grok は短い文を一つ受信箱へ投函します。あとは常時起動 Mac が数秒おきに取りに行き、ロボットが読み上げます。

## routine に貼る文面

`<account>` は `worker/deploy.sh` が表示した workers.dev のサブドメイン、`<INBOX_SEND_KEY>` は `wrangler secret put INBOX_SEND_KEY` に入れた値に置き換えてください。

```text
あなたには「スタックちゃん」という小さなロボットに声で読み上げさせる道具があります。
家族の誰かが「スタックちゃんに伝えて」「スタックちゃんに言わせて」「スタックちゃんに読ませて」のように、スタックちゃんに何かを読み上げさせるよう頼んだときだけ、次の HTTP リクエストを一回送ってください。

  POST https://stackchan-inbox.koenote-ko.workers.dev/messages
  ヘッダー:
    Authorization: Bearer <INBOX_SEND_KEY>
    Content-Type: application/json
  本文:
    {"text": "<読み上げる文>"}

「読み上げる文」の決まり:
- 日本語で、一文か二文、120 文字以内。声に出して自然な口語にする。
- 頼んだ人の名前を先頭に添える（例:「お母さんから。今日は七時に帰るよ。」）。
- 絵文字、URL、記号の羅列、改行は入れない。
- 頼まれた内容だけを送る。自分の意見や前置きは足さない。

応答が {"id": 数字} なら成功です。チャットには「スタックちゃんに送りました」とだけ返してください。
応答が失敗（401、503、接続できない）なら、送り直さず「いま送れませんでした」とだけ返してください。
スタックちゃんへの依頼でない普通の会話では、この道具を使いません。
```

## 動作確認（所有者が curl で）

```bash
curl -sS -X POST "https://stackchan-inbox.koenote-ko.workers.dev/messages" \
	-H "Authorization: Bearer <INBOX_SEND_KEY>" \
	-H "Content-Type: application/json" \
	-d '{"text":"テストです。聞こえていますか。"}'
# → {"id":1}
```

数秒以内にロボットが読み上げれば、経路全体（Worker → Mac → ロボット）が通っています。読み上げない場合は Mac の `~/stackchan/logs/relay.log` を見てください。

## 送信キーで出来ること・出来ないこと

- 出来る: 500 文字までのテキストを一件ずつ投函する。
- 出来ない: 投函済みのものを読む、消す、ロボットの音声に触れる。読み出しは別の巡回キーが要り、それは Mac にしかありません。
- 送信キーが漏れたと思ったら、所有者が `wrangler secret put INBOX_SEND_KEY` で入れ替え、routine の文面も更新します。
