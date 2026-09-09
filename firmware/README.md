# 非公式ファームウェア

このファームウェアの役割は三つだけです。

1. ボタン A を押したら録音する。
2. 録音をリレーの `/device/utterance` へ送る。
3. 返ってきた音声を鳴らす。無音（204）とエラーのときは何も鳴らさない。

音声認識、応答生成、音声合成、認証情報の保持はすべてリレー側です。この端末が持つのは **Wi-Fi 資格情報とリレーの LAN アドレスだけ**です。API キー、トークン、Grok Bot の sender key、Google の認証情報は一切ありません。

経路の全体像は [`../docs/real-device-path.md`](../docs/real-device-path.md) にあります。

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

`config.h` を開き、次の四つだけを設定します。`config.h` は Git 管理外です。

| 項目 | 内容 |
| --- | --- |
| `WIFI_SSID` / `WIFI_PASSWORD` | 家庭内 Wi-Fi の資格情報 |
| `RELAY_HOST` | リレー機のプライベート IPv4（例 `192.168.1.20`）。リレー側の `.env` の `RELAY_HOST` と同じ値にします |
| `RELAY_PORT` | リレーのポート（既定 `8787`） |
| `RECORD_SECONDS` | 一回の録音の長さ。既定は 5 秒 |

リレー側の `.env` の `RELAY_HOST` が `127.0.0.1` のままでは、ロボットから接続できません。リレー機の LAN アドレスに変更してください。

## ビルド

[PlatformIO Core](https://docs.platformio.org/en/latest/core/installation/index.html) が必要です。

```sh
cd firmware
pio run -e core2      # 基板に合わせて core2 / cores3 / fire
```

依存ライブラリ（M5Unified と M5Stack-Avatar）は PlatformIO が自動で取得します。表情や視線の作り込みは行っていません。土台のライブラリが用意している既定の顔をそのまま使います。

## 書き込み

**バックアップの成功行を確認してから**実行してください。

```sh
cd firmware
pio run -e core2 -t upload --upload-port /dev/ttyUSB0
pio device monitor -p /dev/ttyUSB0 -b 115200
```

## 使い方

1. リレーを起動します（`./scripts/run-relay.sh`）。
2. ロボットの電源を入れ、シリアルに Wi-Fi の IP アドレスが出ることを確認します。
3. ボタン A を押します。押した時点から `RECORD_SECONDS` 秒だけ録音します。
4. 録音がリレーへ送られ、返ってきた一文が再生されます。
5. 返答が空、または経路のどこかで失敗したときは何も鳴りません。シリアルに理由が出ます。

## 純正へ戻す

```sh
./scripts/restore-device.sh --port /dev/ttyUSB0 --backup-dir backups/<タイムスタンプ>
```

手順とチェックリストは [`../docs/backup-before-flash.md`](../docs/backup-before-flash.md) にあります。

## 未検証の点

このファームウェアは実機で動作確認していません。ビルドと書き込み、録音・再生の実挙動は所有者の環境での確認が必要です。確認結果は [`../BLOCKER.md`](../BLOCKER.md) の「未検証」節へ追記してください。
