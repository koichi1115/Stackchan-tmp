# 非公式ファームウェア

このファームウェアの役割は四つだけです。

1. マイク音声を常時起動 Mac のリレー（`ws://<RELAY_HOST>:<RELAY_PORT>/device/stream`）へ WebSocket で流し続ける。
2. Mac から届いた音声を鳴らし、口を音量に合わせて動かす。
3. Mac の指示で顔の表情を切り替える。
4. ボタン A が押されたことを Mac へ伝える（ウェイクワードの代わり）。

ウェイクワード判定、音声認識、応答生成、音声合成、認証情報の保持はすべて Mac 側です。この端末が持つのは **Wi-Fi 資格情報とリレーの LAN アドレスだけ**です。API キー、トークン、Grok Bot の sender key、Google の認証情報は一切ありません。

経路の全体像と WebSocket プロトコルは [`../docs/always-on-mac.md`](../docs/always-on-mac.md) にあります。

## ⚠️ 書き込み前に必ずバックアップを取ってください

書き込むと純正ファームウェアは失われます。**先に [`../docs/backup-before-flash.md`](../docs/backup-before-flash.md) の手順を実行し、成功行が出たことを確認してください。** バックアップが無い状態でこの先へ進まないでください。

```sh
./scripts/backup-device.sh --port /dev/ttyUSB0
# 「バックアップ成功。……書き込みへ進んで構いません。」が出るまで先へ進まない
```

## 構成の選択

対象基板は `platformio.ini` の env で選びます。`scripts/backup-device.sh` が出力した `chip-info.txt` のチップ種別と、[M5Stack 製品マニュアル](https://docs.m5stack.com/en/StackChan)の記載を突き合わせて決めてください。

| env | 想定基板 |
| --- | --- |
| `core2` | M5Stack Core2（ESP32） |
| `cores3` | M5Stack CoreS3（ESP32-S3） |
| `fire` | M5Stack Fire（ESP32、PSRAM 付き） |

基板が確定していない状態では書き込まないでください。

## 設定

```sh
cp firmware/include/config.example.h firmware/include/config.h
```

`config.h` を開き、次の三つだけを設定します。`config.h` は Git 管理外です。

| 項目 | 内容 |
| --- | --- |
| `WIFI_SSID` / `WIFI_PASSWORD` | 家庭内 Wi-Fi の資格情報 |
| `RELAY_HOST` | Mac のプライベート IPv4（例 `192.168.1.20`）。リレー側の `.env` の `RELAY_HOST` と同じ値にします |
| `RELAY_PORT` | リレーのポート（既定 `8787`） |

以前の `RECORD_SECONDS` は使わなくなりました。`config.h` に残っていても害はありません。

リレー側の `.env` の `RELAY_HOST` が `127.0.0.1` のままでは、ロボットから接続できません。リレー機の LAN アドレスに変更してください。

## ビルド

[PlatformIO Core](https://docs.platformio.org/en/latest/core/installation/index.html) が必要です。

```sh
cd firmware
pio run -e core2      # 基板に合わせて core2 / cores3 / fire
```

依存ライブラリ（M5Unified、M5Stack-Avatar、WebSockets）は PlatformIO が自動で取得します。表情や視線の作り込みは行っていません。土台のライブラリが用意している既定の顔と表情をそのまま使います。

## 書き込み

**バックアップの成功行を確認してから**実行してください。

```sh
cd firmware
pio run -e core2 -t upload --upload-port /dev/ttyUSB0
pio device monitor -p /dev/ttyUSB0 -b 115200
```

## 使い方

1. Mac でリレーを常駐させます（`mac/deploy.sh --host <user@mac> --setup`。手順は [`../mac/README.md`](../mac/README.md)）。
2. ロボットの電源を入れ、シリアルに Wi-Fi の IP アドレスと `WS: 接続` が出ることを確認します。接続するまで顔は目を閉じて「接続中」と表示します。
3. 「スタックちゃん」と呼びかけます。Mac が聞き取ると「はい？」と返し、続く発話が質問として扱われます。ウェイクワードの後ろに続けて話しても構いません。
4. 返ってきた一文が再生されます。再生中はマイク送信を止め、鳴らし終えると `played` を返して聞き取りに戻ります。
5. ボタン A を押すと `{"type":"button","name":"A"}` を送ります。ウェイクワードが通らないときの代わりです。
6. 返答が空、または経路のどこかで失敗したときは何も鳴りません。シリアルに理由が出ます。

顔の状態は Mac からの `state` で切り替わります。

| 状態 | 顔 | マイク送信 |
| --- | --- | --- |
| `connecting` | 目を閉じる、「接続中」 | しない |
| `listening` | 通常 | する |
| `awake` | 笑顔、「はい？」 | する |
| `thinking` | 困り顔 | する |
| `speaking` | 口を音量に合わせて動かす | しない |

切断したら 3 秒後に再接続します。受け取る WAV の上限は 2 MB で、サンプルレートは WAV ヘッダーに従います。

## 純正へ戻す

```sh
./scripts/restore-device.sh --port /dev/ttyUSB0 --backup-dir backups/<タイムスタンプ>
```

手順とチェックリストは [`../docs/backup-before-flash.md`](../docs/backup-before-flash.md) にあります。

## 未検証の点

2026-09-10 に `cores3` env でビルドし、実機（M5Stack CoreS3、COM7）へ書き込み、起動後に `Wi-Fi: 192.168.0.11` が出て LAN から ping に応答することまで確認したのは、一つ前の HTTP 版です。WebSocket 版はビルドが通ることまでで、マイクの連続送信・再生・表情の実挙動は実機で確認していません。確認結果は [`../BLOCKER.md`](../BLOCKER.md) の「未検証」節へ追記してください。
