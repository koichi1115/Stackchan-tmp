# StackChan World の公開フック調査

調査日は 2026 年 9 月 5 日です。対象は M5Stack 製 StackChan の出荷時ファームウェアと StackChan World だけです。カスタムファームウェア、フォーク、サイドロード、未公開 API、StackChan World からの離脱は対象外です。

## 結論

| 必要な経路 | 文書化された仕組み | 判定 |
| --- | --- | --- |
| 認識済み音声またはボタン起点のテキストを、設定可能な HTTP エンドポイントまたは Webhook へ送る | なし | 未対応 |
| 外部からテキストを受け取り、StackChan に発話させる HTTP、TTS、または speak API | なし | 未対応 |

どちらも必要なため、実機アダプターは実装しません。リレーはモックの `StackChanListener` と `StackChanSpeaker` だけを使います。

## 確認した動作

[M5Stack の製品マニュアル](https://docs.m5stack.com/en/StackChan)は、音声ウェイクワードまたは画面タップで AI Agent の会話を開始すると説明しています。StackChan World で設定できる項目は AI モデル、音声、認識速度、性格、記憶、Wi-Fi、MCP などです。同マニュアルが説明する Home Assistant MCP は、AI Agent が Home Assistant の機器を操作するための接続です。認識済みテキストを任意の HTTP エンドポイントへ転送する Webhook ではありません。

[M5Stack の公式オープンソース一覧](https://github.com/m5stack/StackChan/tree/1b5765599fba8aaad1811d9a79358ccc7051f5f3)も確認しました。このリポジトリは、公開内容がリリース済みファームウェアとアプリより遅れる場合があると明記しています。そのため、ソース内の未文書化機能を出荷状態の契約とは扱いません。

[公式サーバー文書の REST API 一覧](https://github.com/m5stack/StackChan/blob/1b5765599fba8aaad1811d9a79358ccc7051f5f3/server/README.MD#10-api-reference-by-module)には、端末、ダンス、投稿、ファイル、アプリ一覧、XiaoZhi トークンの API があります。テキストを受け取って発話する HTTP、TTS、または speak API はありません。

[公式サーバー文書の WebSocket プロトコル](https://github.com/m5stack/StackChan/blob/1b5765599fba8aaad1811d9a79358ccc7051f5f3/server/README.MD#11-websocket-protocol)には、音声フレームと `TextMessage` が列挙されています。ただし、`TextMessage` を発話させる契約は記載されていません。HTTP API でもありません。未文書化の挙動に依存できないため、受信発話フックとは判定しません。
