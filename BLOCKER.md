# 実機接続の阻害要因

次の経路は StackChan World の公開文書で対応を確認できません。

1. `StackChan World -> 設定可能な HTTP エンドポイントまたは Webhook`。認識済み音声またはボタン起点のテキストを送る公開設定がありません。
2. `リレー -> StackChan World`。テキストを受け取り、実機に発話させる公開 HTTP、TTS、または speak API がありません。

確認した資料は、[M5Stack の製品マニュアル](https://docs.m5stack.com/en/StackChan)、[M5Stack の公式リポジトリ](https://github.com/m5stack/StackChan/tree/1b5765599fba8aaad1811d9a79358ccc7051f5f3)、[公式サーバーの REST API 一覧](https://github.com/m5stack/StackChan/blob/1b5765599fba8aaad1811d9a79358ccc7051f5f3/server/README.MD#10-api-reference-by-module)、[公式サーバーの WebSocket プロトコル](https://github.com/m5stack/StackChan/blob/1b5765599fba8aaad1811d9a79358ccc7051f5f3/server/README.MD#11-websocket-protocol)です。

実機接続は、ベンダーが認識済みテキストを送れる正式な送信経路を提供するまで待ちます。受信発話にも正式な対応経路が必要です。カスタムファームウェア、フォーク、サイドロード、未公開エンドポイント、StackChan World からの離脱では回避しません。
