# 実機接続の判断の記録

## 1. 経緯（s1 の調査結果）

s1 では、出荷時ファームウェアと StackChan World だけを対象に実機接続の可否を調べ、次の二点が公開文書で確認できないと結論しました。

1. `StackChan World -> 設定可能な HTTP エンドポイントまたは Webhook`。認識済み音声またはボタン起点のテキストを送る公開設定がありません。
2. `リレー -> StackChan World`。テキストを受け取り、実機に発話させる公開 HTTP、TTS、または speak API がありません。

確認した資料は次のとおりです。

- [M5Stack の製品マニュアル](https://docs.m5stack.com/en/StackChan)
- [M5Stack の公式リポジトリ](https://github.com/m5stack/StackChan/tree/1b5765599fba8aaad1811d9a79358ccc7051f5f3)
- [公式サーバーの REST API 一覧](https://github.com/m5stack/StackChan/blob/1b5765599fba8aaad1811d9a79358ccc7051f5f3/server/README.MD#10-api-reference-by-module)
- [公式サーバーの WebSocket プロトコル](https://github.com/m5stack/StackChan/blob/1b5765599fba8aaad1811d9a79358ccc7051f5f3/server/README.MD#11-websocket-protocol)

調査の詳細は [`docs/stackchan-world-hooks.md`](docs/stackchan-world-hooks.md) にあります。この結論は現在も有効です。**純正のままでは、この用途は実現できません。**

s1 の時点の判断は「ベンダーが正式な送出・受信経路を提供するまで待つ」でした。

## 2. 所有者の決定（s2）

やりたいことは公式の StackChan World では実現できないため、**待機を終了します。純正ファームウェアは破棄してよい**というのが所有者の最終方針です。

条件は二つです。

- **書き込み前のバックアップは必須**であり、検証済みであること。
- s1 の安全条件「公開受信口を作らない」は維持すること。

## 3. 選んだ経路

純正ファームウェアを、次のことだけを行う非公式ファームウェアに置き換えます。

1. 一回ぶん録音する。
2. 家庭内 LAN のリレーへ音声を送る。
3. 返ってきた音声を鳴らす。

知能と認証情報はすべてリレー側に残します。経路の定義は [`docs/real-device-path.md`](docs/real-device-path.md)、バックアップ手順は [`docs/backup-before-flash.md`](docs/backup-before-flash.md) にあります。

### ファームウェアの土台

**M5Unified + M5Stack-Avatar を土台にした最小の PlatformIO プロジェクト**（`firmware/`）を選びました。

検討した選択肢と判断は次のとおりです。

| 候補 | 採否 | 理由 |
| --- | --- | --- |
| M5Unified + M5Stack-Avatar の最小構成（採用） | 採用 | マイク・スピーカー・画面の抽象化が一つのライブラリで揃い、基板差（Core2 / CoreS3 / Fire）を env の切り替えで吸収できます。今回必要なのは録音・HTTP・再生だけで、土台が用意している顔以外は何も足していません。 |
| 公式 m5stack/StackChan のソースへ最小アダプターを追加 | 不採用 | 公式リポジトリは「公開内容がリリース済みファームウェアより遅れることがある」と明記しており、土台としての再現性が担保できません。必要のない World 連携部分まで抱え込みます。 |
| コミュニティの stack-chan（meganetaaan）一式 | 不採用 | サーボ、視線、表情、TTS 連携など、このスライスに不要な機能が多く含まれます。今回は「録音して送る、鳴らす」だけに絞ります。 |

土台の trade-off は、**M5Unified の API に依存すること**と、**基板ごとにマイク・スピーカーの挙動差があること**です。後者は実機での確認が必要です。

## 4. 代償（trade-off）

この経路を選んだことで、次を失います。

- **StackChan World の機能一式**。AI Agent の会話、モデル選択、性格・記憶の設定、ウェイクワード、MCP 連携、アプリからの管理は使えなくなります。
- **純正の音声**。変更後の声はリレー上の VOICEVOX ENGINE による非公式 TTS の声で、純正の声とは異なります。
- **サポートと保証**。非公式ファームウェアを書き込んだ状態は、メーカーの想定外です。サポートを受けられない、保証の対象外になる可能性があります。
- **純正ファームウェアの再入手性**。M5Stack が純正イメージを再配布する保証はありません。**だからバックアップが必須です。**

戻す手段は用意しています。`scripts/backup-device.sh` が SHA-256 で二重検証したフラッシュ全体のダンプを取り、`scripts/restore-device.sh` がそれをオフセット 0 へ書き戻して読み返し照合します。消去は行いません。

得るものは、公開受信口を一つも作らずに、家庭内 LAN だけで一往復の対話が成立することです。

## 5. まだ検証できていないこと

以下は所有者が実機で確認するまで未確定です。クラウド側では確認できません。

- ~~`firmware/` が実際にビルドできること。~~ **2026-09-10 確認済み。** PlatformIO 6.1.19、espressif32@6.9.0、env `cores3` でビルド成功（Flash 1,092,105 バイト、RAM 49,304 バイト）。COM7 へ書き込み後、起動ログに `Wi-Fi: 192.168.0.11` と「ボタン A で一往復します。」が出た。
- ~~対象基板・MCU・フラッシュ容量の確定値。~~ **2026-09-10 確認済み。** 実測 ESP32-S3 (QFN56) rev v0.2 / 16MB、[M5Stack 製品マニュアル](https://docs.m5stack.com/en/StackChan)の記載（CoreS3、ESP32-S3、16MB Flash、8MB PSRAM）と一致。転記先は `docs/real-device-path.md`。
- `scripts/backup-device.sh` は **2026-09-10 に実機で成功行まで確認済み**（esptool v5.4.0、純正 stack-chan 1.4.2 / 1.2.6 を `backups/20260909T233725Z/` に保存、SHA-256 二回一致）。`scripts/restore-device.sh` は**まだ実機で実行していません**。esptool v5 の `Chip type:` 出力に合わせて両スクリプトのチップ名解釈を修正済み。
- マイクの録音品質が音声認識に足りること。`RECORD_SECONDS` の妥当性。
- 返答音声の再生品質と、PSRAM 容量に対する応答音声の大きさ。
- ~~whisper.cpp サーバーと VOICEVOX ENGINE を実際につないだときの往復時間。~~ **2026-09-11 に Mac 上で実測、1.1〜1.25 秒**（合成音声、Grok はモック）。5b を参照。

検証済みなのは、リレー側のオフライン一往復（モック STT・モック TTS・モック Webhook）と、無音の扱いです。実機で一往復が成立したかどうかは、この文書に書かれていません。

## 5b. 所有者の決定（s4、2026-09-10）

s3 の「ボタン A で一往復」を確認したあと、所有者は次を目標に定めました。

1. ウェイクワード「スタックちゃん」で起動する。
2. Grok Bot から指示された文（主に家族チャット）をロボットが読み上げる。
3. ウェイクワード → STT → Grok → TTS の会話をモックではなく本物で行う。
4. リレーはこの PC ではなく常時起動の Mac（Apple Silicon）に置く。

選んだ方式は次のとおりです（所有者が選択）。

- Grok からロボットへ届ける経路は **Cloudflare Worker の受信箱**。Grok が投函し、Mac が外向き HTTPS で取りに行く。家に受信口は開けない。これは s1 の「公開受信口を作らない」を家のネットワークについて維持したまま、家の外に小さな郵便受けを一つ置く変更である。
- ウェイクワードは **Mac 側で検出**（VAD → whisper.cpp の文字起こし → 先頭一致）。ロボットに鍵や追加のアカウントを持たせない。
- 配置はこの PC から **SSH** で行う。

設計と手順は [`docs/always-on-mac.md`](docs/always-on-mac.md)。実装はリレー（`src/stackchan_grok_relay/`）、Worker（`worker/`）、Mac のセットアップ（`mac/`）、WebSocket 版ファームウェア（`firmware/`）。

### s4 で未検証のこと

- ~~Mac 上での whisper.cpp / VOICEVOX / リレーの常駐と、実音声での認識精度・往復時間。~~ **2026-09-11 確認済み。** Mac mini M1 に whisper-cpp 1.9.2（`ggml-small.bin`、Metal）、VOICEVOX ENGINE 0.25.2、リレーを launchd で常駐させ、合成音声で一往復 1.1〜1.25 秒。whisper がウェイクワードを別語に書き起こす問題を見つけ、初期プロンプト（`STT_PROMPT`）で解消しました。詳細は [`docs/always-on-mac.md`](docs/always-on-mac.md) の「実音声で分かったこと」。
- **実際のマイクを通した認識精度。** 上の検証は VOICEVOX の合成音声をシミュレーターへ流したもので、室内の雑音・距離・実機のマイク特性は含みません。
- 実機でのマイク連続送信と VAD の閾値（`VAD_THRESHOLD` 既定 600 は仮の値）。
- ~~受信箱の Worker の本番配置~~ **2026-09-11 配置済み。投函 → 読み上げ → ack を実機で確認。** Grok routine からの投函はまだ。
- **Grok の会話用 Webhook（`grok.com/webhook/automation/<id>`）が本文・鍵の渡し方によらず HTTP 400 を返す。** routine 画面の呼び出し例と突き合わせるまで会話経路は未成立。読み上げ経路とウェイクワード応答（「はい？」）は動作済み。
- WebSocket 版ファームウェアの実機動作（ビルドは通過、書き込みは未実施）。
- 会話用 Grok Webhook の URL と sender key は未設定のままです（`.env` は例のプレースホルダー）。上の往復検証はモック Webhook で行いました。

## 6. やらないこと

- 純正フックの提供を待つこと、監視すること、それに依存すること。
- 公開受信口、ポート転送、トンネル、クラウド中継の追加。
- ロボット側への鍵・トークンの配置。
- 会話履歴、カレンダー、視覚、表情エンジンの追加。
