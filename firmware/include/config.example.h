// このファイルを include/config.h へコピーして書き換えてください。
// config.h は Git 管理外です。
//
// ここに置いてよいのは Wi-Fi 資格情報とリレーの LAN アドレスだけです。
// API キー、トークン、Grok Bot の sender key、Google の認証情報は
// 絶対にここへ書かないでください。すべてリレーの .env にあります。

#pragma once

// 家庭内 Wi-Fi
#define WIFI_SSID "your-wifi-ssid"
#define WIFI_PASSWORD "your-wifi-password"

// リレーの LAN アドレス（RELAY_HOST に設定したプライベート IPv4）
#define RELAY_HOST "192.168.1.20"
#define RELAY_PORT 8787

// 一回の録音の長さ（秒）
#define RECORD_SECONDS 5
