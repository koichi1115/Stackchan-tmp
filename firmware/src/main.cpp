// StackChan 非公式ファームウェア（最小構成）
//
// できること
//   1. ボタン A を押している間、マイクで録音する。
//   2. 録音を WAV にしてリレーの /device/utterance へ POST する。
//   3. 返ってきた WAV を鳴らす。
//
// できないこと（意図的）
//   - 音声認識、応答生成、音声合成。すべてリレー側で行います。
//   - 認証情報の保持。この端末が持つのは Wi-Fi 資格情報とリレーのアドレスだけです。
//   - 会話の記憶。一往復ごとに独立しています。
//   - エラーの発話。失敗したときは黙ります。

#include <Arduino.h>
#include <HTTPClient.h>
#include <M5Unified.h>
#include <WiFi.h>

#include <Avatar.h>

#include "config.h"

namespace {

constexpr uint32_t kRecordSampleRate = 16000;
constexpr size_t kRecordSamples = kRecordSampleRate * RECORD_SECONDS;
constexpr size_t kMaxReplyBytes = 2 * 1024 * 1024;
constexpr uint32_t kHttpTimeoutMs = 30000;

m5avatar::Avatar avatar;

int16_t* record_buffer = nullptr;
uint8_t* request_buffer = nullptr;  // WAV ヘッダー + 録音
uint8_t* reply_buffer = nullptr;

void writeLe32(uint8_t* out, uint32_t value) {
  out[0] = value & 0xff;
  out[1] = (value >> 8) & 0xff;
  out[2] = (value >> 16) & 0xff;
  out[3] = (value >> 24) & 0xff;
}

void writeLe16(uint8_t* out, uint16_t value) {
  out[0] = value & 0xff;
  out[1] = (value >> 8) & 0xff;
}

// 録音の先頭 44 バイトへ WAV ヘッダーを書き込みます。
void writeWavHeader(uint8_t* out, uint32_t data_bytes, uint32_t sample_rate) {
  memcpy(out + 0, "RIFF", 4);
  writeLe32(out + 4, 36 + data_bytes);
  memcpy(out + 8, "WAVEfmt ", 8);
  writeLe32(out + 16, 16);
  writeLe16(out + 20, 1);  // PCM
  writeLe16(out + 22, 1);  // モノラル
  writeLe32(out + 24, sample_rate);
  writeLe32(out + 28, sample_rate * 2);
  writeLe16(out + 32, 2);
  writeLe16(out + 34, 16);
  memcpy(out + 36, "data", 4);
  writeLe32(out + 40, data_bytes);
}

// 応答 WAV から fmt と data を取り出します。見つからなければ false を返します。
bool parseWav(const uint8_t* wav, size_t length, uint32_t* sample_rate, uint16_t* channels,
              const int16_t** samples, size_t* sample_count) {
  if (length < 44 || memcmp(wav, "RIFF", 4) != 0 || memcmp(wav + 8, "WAVE", 4) != 0) {
    return false;
  }
  size_t offset = 12;
  bool have_fmt = false;
  while (offset + 8 <= length) {
    const char* chunk_id = reinterpret_cast<const char*>(wav + offset);
    uint32_t chunk_size = wav[offset + 4] | (wav[offset + 5] << 8) | (wav[offset + 6] << 16) |
                          (static_cast<uint32_t>(wav[offset + 7]) << 24);
    size_t body = offset + 8;
    if (body + chunk_size > length) {
      chunk_size = length - body;
    }
    if (memcmp(chunk_id, "fmt ", 4) == 0 && chunk_size >= 16) {
      *channels = wav[body + 2] | (wav[body + 3] << 8);
      *sample_rate = wav[body + 4] | (wav[body + 5] << 8) | (wav[body + 6] << 16) |
                     (static_cast<uint32_t>(wav[body + 7]) << 24);
      uint16_t bits = wav[body + 14] | (wav[body + 15] << 8);
      if (bits != 16) {
        return false;
      }
      have_fmt = true;
    } else if (memcmp(chunk_id, "data", 4) == 0) {
      if (!have_fmt) {
        return false;
      }
      *samples = reinterpret_cast<const int16_t*>(wav + body);
      *sample_count = chunk_size / 2;
      return *sample_count > 0;
    }
    offset = body + chunk_size + (chunk_size & 1);
  }
  return false;
}

uint8_t* allocate(size_t bytes) {
  uint8_t* buffer = static_cast<uint8_t*>(heap_caps_malloc(bytes, MALLOC_CAP_SPIRAM));
  if (buffer == nullptr) {
    buffer = static_cast<uint8_t*>(malloc(bytes));
  }
  return buffer;
}

void connectWifi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  for (int attempt = 0; attempt < 60 && WiFi.status() != WL_CONNECTED; ++attempt) {
    delay(500);
  }
  Serial.printf("Wi-Fi: %s\n", WiFi.status() == WL_CONNECTED ? WiFi.localIP().toString().c_str()
                                                             : "未接続");
}

size_t recordUtterance() {
  M5.Speaker.end();
  if (!M5.Mic.begin()) {
    Serial.println("マイクを開始できませんでした。");
    return 0;
  }
  Serial.println("録音中です。");
  M5.Mic.record(record_buffer, kRecordSamples, kRecordSampleRate);
  while (M5.Mic.isRecording()) {
    M5.update();
    delay(1);
  }
  M5.Mic.end();

  const size_t data_bytes = kRecordSamples * sizeof(int16_t);
  writeWavHeader(request_buffer, data_bytes, kRecordSampleRate);
  memcpy(request_buffer + 44, record_buffer, data_bytes);
  return 44 + data_bytes;
}

// 返答音声を鳴らします。応答が無い（204）ときは何も鳴らしません。
void exchangeWithRelay(size_t request_bytes) {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("Wi-Fi が未接続のため送信しません。");
    return;
  }

  String url = String("http://") + RELAY_HOST + ":" + String(RELAY_PORT) + "/device/utterance";
  HTTPClient http;
  http.setTimeout(kHttpTimeoutMs);
  if (!http.begin(url)) {
    Serial.println("リレーへの接続を開始できませんでした。");
    return;
  }
  http.addHeader("Content-Type", "audio/wav");
  const char* collect[] = {"X-Spoken-Text-Base64"};
  http.collectHeaders(collect, 1);

  int status = http.POST(request_buffer, request_bytes);
  if (status != HTTP_CODE_OK) {
    // 204（無音）とエラーはどちらも「何も鳴らさない」で同じ扱いにします。
    Serial.printf("応答 %d のため無音にします。\n", status);
    http.end();
    return;
  }

  Serial.printf("発話(base64): %s\n", http.header("X-Spoken-Text-Base64").c_str());

  int content_length = http.getSize();
  size_t received = 0;
  WiFiClient* stream = http.getStreamPtr();
  const size_t limit = (content_length > 0 && static_cast<size_t>(content_length) < kMaxReplyBytes)
                           ? static_cast<size_t>(content_length)
                           : kMaxReplyBytes;
  uint32_t deadline = millis() + kHttpTimeoutMs;
  while (received < limit && millis() < deadline && (http.connected() || stream->available())) {
    int available = stream->available();
    if (available <= 0) {
      delay(1);
      continue;
    }
    int read = stream->readBytes(reply_buffer + received, min<size_t>(available, limit - received));
    if (read <= 0) {
      break;
    }
    received += read;
  }
  http.end();

  uint32_t sample_rate = 0;
  uint16_t channels = 1;
  const int16_t* samples = nullptr;
  size_t sample_count = 0;
  if (!parseWav(reply_buffer, received, &sample_rate, &channels, &samples, &sample_count)) {
    Serial.println("応答を WAV として読めなかったため無音にします。");
    return;
  }

  M5.Mic.end();
  M5.Speaker.begin();
  M5.Speaker.playRaw(samples, sample_count, sample_rate, channels == 2);
  while (M5.Speaker.isPlaying()) {
    M5.update();
    delay(1);
  }
}

}  // namespace

void setup() {
  auto cfg = M5.config();
  M5.begin(cfg);
  Serial.begin(115200);

  avatar.init();

  record_buffer = reinterpret_cast<int16_t*>(allocate(kRecordSamples * sizeof(int16_t)));
  request_buffer = allocate(44 + kRecordSamples * sizeof(int16_t));
  reply_buffer = allocate(kMaxReplyBytes);
  if (record_buffer == nullptr || request_buffer == nullptr || reply_buffer == nullptr) {
    Serial.println("音声バッファを確保できませんでした。RECORD_SECONDS を減らしてください。");
  }

  connectWifi();
  Serial.println("ボタン A で一往復します。");
}

void loop() {
  M5.update();
  if (M5.BtnA.wasPressed() && record_buffer != nullptr && request_buffer != nullptr &&
      reply_buffer != nullptr) {
    size_t request_bytes = recordUtterance();
    if (request_bytes > 0) {
      exchangeWithRelay(request_bytes);
    }
  }
  delay(10);
}
