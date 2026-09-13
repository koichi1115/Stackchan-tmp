// StackChan 非公式ファームウェア（常時起動 Mac との WebSocket 会話）
//
// できること
//   1. マイク音声（16 kHz・モノラル・16 bit PCM）を Mac のリレーへ WebSocket で流し続ける。
//   2. Mac から届いた WAV を鳴らし、口を音量に合わせて動かす。
//   3. Mac からの指示で顔の表情を切り替える。
//   4. ボタン A を押したことを Mac へ伝える（ウェイクワードの代わり）。
//
// できないこと（意図的）
//   - 音声認識、ウェイクワード判定、応答生成、音声合成。すべて Mac 側で行います。
//   - 認証情報の保持。この端末が持つのは Wi-Fi 資格情報とリレーの LAN アドレスだけです。
//   - 会話の記憶。一往復ごとに独立しています。
//   - エラーの発話。失敗したときは黙ります。
//
// プロトコルと状態遷移は docs/always-on-mac.md にあります。

#include <Arduino.h>
#include <M5Unified.h>
#include <WebSocketsClient.h>
#include <WiFi.h>

#include <Avatar.h>

#include "config.h"

namespace {

constexpr char kStreamPath[] = "/device/stream";
constexpr char kFirmwareVersion[] = "0.2.0";
#if defined(ARDUINO_M5STACK_CORES3)
constexpr char kDeviceName[] = "cores3";
#elif defined(ARDUINO_M5STACK_Core2)
constexpr char kDeviceName[] = "core2";
#elif defined(ARDUINO_M5STACK_FIRE)
constexpr char kDeviceName[] = "fire";
#else
constexpr char kDeviceName[] = "m5stack";
#endif

constexpr uint32_t kSampleRate = 16000;
constexpr size_t kMicFrameSamples = 1024;  // 1 フレーム = 2048 バイト
constexpr size_t kMicBufferCount = 3;
constexpr size_t kMaxWavBytes = 2 * 1024 * 1024;
constexpr size_t kPlayChunkSamples = 4096;
constexpr uint32_t kReconnectMs = 3000;
constexpr float kMouthFullScaleRms = 4000.0f;
// スピーカーの音量（0〜255）。config.h で SPEAKER_VOLUME を定義すれば上書きできる。
// M5Unified の既定値は控えめで、VOICEVOX の出力だと聞き取りにくい。
#ifndef SPEAKER_VOLUME
#define SPEAKER_VOLUME 220
#endif
constexpr uint8_t kSpeakerVolume = SPEAKER_VOLUME;

enum class State { Connecting, Listening, Awake, Thinking, Speaking };

m5avatar::Avatar avatar;
WebSocketsClient ws;
State state = State::Connecting;

// マイクの録音バッファ。M5.Mic は内部に二つの受付枠を持ち、record() は枠が空くまで待ってから
// 予約して戻る。枠が空くのは「二回前に渡したバッファ」が録り終わったときなので、三つを
// 順繰りに使えば record(buf[k]) が戻った時点で buf[(k+1)%3]（= buf[k-2]）は完成している。
// buf[k-1] は録音中、buf[k] は予約済み。この順序は M5Unified の Mic_Class.cpp
// （_rec_raw と mic_task の flip 処理）で確認した。
int16_t mic_buffer[kMicBufferCount][kMicFrameSamples];
size_t mic_index = 0;
bool mic_running = false;

// Mac から届く WAV。speak で告げられたバイト数が揃うまで連結する。
uint8_t* wav_buffer = nullptr;
size_t wav_expected = 0;
size_t wav_received = 0;
bool wav_discard = false;  // 上限超過など、受け取るだけで鳴らさない
bool wav_ready = false;    // 揃ったので loop() で鳴らす
bool playing = false;

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

// JSON の "key": の直後にある値の先頭を返します。無ければ nullptr。
const char* jsonValue(const char* json, const char* key) {
  String pattern = String("\"") + key + "\"";
  // 同じ文字列が値側にも現れうる（例 {"type":"state","state":"awake"}）ので、
  // 直後に ':' が続く出現を見つけるまで探し続ける。
  const char* p = json;
  while ((p = strstr(p, pattern.c_str())) != nullptr) {
    const char* q = p + pattern.length();
    while (*q == ' ' || *q == '\t' || *q == '\r' || *q == '\n') ++q;
    if (*q == ':') {
      ++q;
      while (*q == ' ' || *q == '\t' || *q == '\r' || *q == '\n') ++q;
      return q;
    }
    p += pattern.length();
  }
  return nullptr;
}

// 文字列値を取り出します。エスケープは解釈しません（ログ表示にしか使わないため）。
bool jsonString(const char* json, const char* key, String* out) {
  const char* p = jsonValue(json, key);
  if (p == nullptr || *p != '"') {
    return false;
  }
  ++p;
  *out = "";
  while (*p != '\0' && *p != '"') {
    if (*p == '\\' && p[1] != '\0') {
      *out += *p++;
    }
    *out += *p++;
  }
  return *p == '"';
}

bool jsonUnsigned(const char* json, const char* key, size_t* out) {
  const char* p = jsonValue(json, key);
  if (p == nullptr || *p < '0' || *p > '9') {
    return false;
  }
  *out = strtoul(p, nullptr, 10);
  return true;
}

const char* stateName(State s) {
  switch (s) {
    case State::Connecting: return "connecting";
    case State::Listening: return "listening";
    case State::Awake: return "awake";
    case State::Thinking: return "thinking";
    case State::Speaking: return "speaking";
  }
  return "?";
}

void startMic() {
  M5.Speaker.end();
  if (!M5.Mic.begin()) {
    Serial.println("マイクを開始できませんでした。");
    return;
  }
  // 二枠を先に埋めておく。以後は record(buf[k]) → 送信 buf[(k+1)%3] の順で回す。
  M5.Mic.record(mic_buffer[0], kMicFrameSamples, kSampleRate);
  M5.Mic.record(mic_buffer[1], kMicFrameSamples, kSampleRate);
  mic_index = 2;
  mic_running = true;
}

void stopMic() {
  if (!mic_running) {
    return;
  }
  M5.Mic.end();
  mic_running = false;
}

bool stateSendsMic(State s) {
  return s == State::Listening || s == State::Awake || s == State::Thinking;
}

void setState(State next) {
  if (next != state) {
    Serial.printf("状態: %s → %s\n", stateName(state), stateName(next));
  }
  state = next;
  if (stateSendsMic(next)) {
    if (!mic_running) startMic();
  } else {
    stopMic();
  }
  avatar.setMouthOpenRatio(0.0f);
  switch (next) {
    case State::Connecting:
      avatar.setExpression(m5avatar::Expression::Sleepy);
      avatar.setSpeechText("接続中");
      break;
    case State::Listening:
      avatar.setExpression(m5avatar::Expression::Neutral);
      avatar.setSpeechText("");
      break;
    case State::Awake:
      avatar.setExpression(m5avatar::Expression::Happy);
      avatar.setSpeechText("はい？");
      break;
    case State::Thinking:
      avatar.setExpression(m5avatar::Expression::Doubt);
      avatar.setSpeechText("");
      break;
    case State::Speaking:
      avatar.setExpression(m5avatar::Expression::Happy);
      avatar.setSpeechText("");
      break;
  }
}

void resetWav() {
  wav_expected = 0;
  wav_received = 0;
  wav_discard = false;
  wav_ready = false;
}

void sendHello() {
  String hello = String("{\"type\":\"hello\",\"device\":\"") + kDeviceName +
                 "\",\"sample_rate\":" + String(kSampleRate) + ",\"firmware\":\"" +
                 kFirmwareVersion + "\"}";
  ws.sendTXT(hello);
}

void handleText(const uint8_t* payload, size_t length) {
  String json;
  json.reserve(length);
  json.concat(reinterpret_cast<const char*>(payload), length);

  String type;
  if (!jsonString(json.c_str(), "type", &type)) {
    return;
  }

  if (type == "state") {
    String name;
    if (!jsonString(json.c_str(), "state", &name)) {
      return;
    }
    if (state == State::Speaking || wav_expected > 0) {
      // 鳴らし終えて played を返すまでは顔を変えない。
      return;
    }
    if (name == "listening") {
      setState(State::Listening);
    } else if (name == "awake") {
      setState(State::Awake);
    } else if (name == "thinking") {
      setState(State::Thinking);
    } else if (name == "speaking") {
      // speaking への入り方は speak 受信なので、ここでは顔だけ合わせマイクは止めない。
      avatar.setExpression(m5avatar::Expression::Happy);
      avatar.setSpeechText("");
    }
    return;
  }

  if (type == "speak") {
    size_t bytes = 0;
    String text;
    jsonString(json.c_str(), "text", &text);
    if (!jsonUnsigned(json.c_str(), "bytes", &bytes) || bytes == 0) {
      Serial.println("speak の bytes が不正なため無視します。");
      return;
    }
    if (state == State::Speaking || wav_expected > 0 || playing) {
      Serial.println("再生中に speak が届いたため無視します。");
      return;
    }
    resetWav();
    wav_expected = bytes;
    wav_discard = (bytes > kMaxWavBytes) || wav_buffer == nullptr;
    if (wav_discard) {
      Serial.printf("WAV %u バイトは上限を超えるため鳴らしません。\n", static_cast<unsigned>(bytes));
    }
    Serial.printf("speak: %u バイト \"%s\"\n", static_cast<unsigned>(bytes), text.c_str());
    setState(State::Speaking);
    return;
  }
}

void handleBinary(const uint8_t* payload, size_t length) {
  if (wav_expected == 0 || wav_ready) {
    return;  // speak を待っていないときの音声は無視する
  }
  size_t remain = wav_expected - wav_received;
  size_t take = length < remain ? length : remain;
  if (!wav_discard) {
    memcpy(wav_buffer + wav_received, payload, take);
  }
  wav_received += take;
  if (wav_received >= wav_expected) {
    wav_ready = true;
  }
}

void onWsEvent(WStype_t type, uint8_t* payload, size_t length) {
  switch (type) {
    case WStype_CONNECTED:
      Serial.printf("WS: 接続 ws://%s:%d%s\n", RELAY_HOST, RELAY_PORT, kStreamPath);
      resetWav();
      sendHello();
      setState(State::Listening);
      break;
    case WStype_DISCONNECTED:
      if (state != State::Connecting) {
        Serial.println("WS: 切断。3 秒後に再接続します。");
      }
      resetWav();
      setState(State::Connecting);
      break;
    case WStype_TEXT:
      handleText(payload, length);
      break;
    case WStype_BIN:
      handleBinary(payload, length);
      break;
    case WStype_ERROR:
      Serial.println("WS: エラー");
      break;
    default:
      break;
  }
}

// 録り終わった一枠を送り、次の一枠を予約します。
void streamMicFrame() {
  M5.Mic.record(mic_buffer[mic_index], kMicFrameSamples, kSampleRate);
  const size_t done = (mic_index + 1) % kMicBufferCount;
  ws.sendBIN(reinterpret_cast<const uint8_t*>(mic_buffer[done]),
             kMicFrameSamples * sizeof(int16_t));
  mic_index = done;
}

float mouthRatio(const int16_t* samples, size_t count) {
  if (count == 0) {
    return 0.0f;
  }
  double sum = 0;
  for (size_t i = 0; i < count; ++i) {
    sum += static_cast<double>(samples[i]) * samples[i];
  }
  float rms = sqrtf(static_cast<float>(sum / count));
  float ratio = rms / kMouthFullScaleRms;
  return ratio > 1.0f ? 1.0f : ratio;
}

// 揃った WAV を鳴らします。読めなければ黙ります。
void playWav() {
  uint32_t sample_rate = 0;
  uint16_t channels = 1;
  const int16_t* samples = nullptr;
  size_t sample_count = 0;
  if (!parseWav(wav_buffer, wav_received, &sample_rate, &channels, &samples, &sample_count)) {
    Serial.println("WAV として読めなかったため無音にします。");
    return;
  }
  Serial.printf("再生: %u Hz %u ch %u サンプル\n", static_cast<unsigned>(sample_rate), channels,
                static_cast<unsigned>(sample_count));

  stopMic();
  M5.Speaker.begin();
  M5.Speaker.setVolume(kSpeakerVolume);
  playing = true;

  // 約 4096 サンプルずつ渡す。playRaw は二枠のどちらかが空くまで待つので、i 番目を渡し終えた
  // 時点で鳴っているのは i-1 番目。口はその音量に合わせる。
  const bool stereo = (channels == 2);
  float prev_ratio = 0.0f;
  for (size_t offset = 0; offset < sample_count; offset += kPlayChunkSamples) {
    size_t count = sample_count - offset;
    if (count > kPlayChunkSamples) count = kPlayChunkSamples;
    float ratio = mouthRatio(samples + offset, count);
    if (offset == 0) prev_ratio = ratio;
    M5.Speaker.playRaw(samples + offset, count, sample_rate, stereo, 1, -1, false);
    avatar.setMouthOpenRatio(prev_ratio);
    prev_ratio = ratio;
    M5.update();
    ws.loop();
  }
  avatar.setMouthOpenRatio(prev_ratio);
  while (M5.Speaker.isPlaying()) {
    M5.update();
    ws.loop();
    delay(1);
  }
  avatar.setMouthOpenRatio(0.0f);
  playing = false;
}

void connectWifi() {
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  for (int attempt = 0; attempt < 60 && WiFi.status() != WL_CONNECTED; ++attempt) {
    delay(500);
  }
  Serial.printf("Wi-Fi: %s\n", WiFi.status() == WL_CONNECTED ? WiFi.localIP().toString().c_str()
                                                             : "未接続");
}

}  // namespace

void setup() {
  auto cfg = M5.config();
  M5.begin(cfg);
  Serial.begin(115200);

  avatar.setSpeechFont(&fonts::lgfxJapanGothic_16);
  avatar.init();
  setState(State::Connecting);

  wav_buffer = allocate(kMaxWavBytes);
  if (wav_buffer == nullptr) {
    Serial.println("WAV バッファを確保できませんでした。音声は鳴らしません。");
  }

  connectWifi();

  ws.onEvent(onWsEvent);
  ws.setReconnectInterval(kReconnectMs);
  ws.begin(RELAY_HOST, RELAY_PORT, kStreamPath);
  Serial.printf("WS: ws://%s:%d%s へ接続します。\n", RELAY_HOST, RELAY_PORT, kStreamPath);
}

void loop() {
  M5.update();
  ws.loop();

  // CoreS3（StackChan）には物理ボタンが無く、M5Unified の仮想ボタン A も既定では画面の外側
  // （y >= 240）にしか反応しない。そこで画面のどこかをタップしたら「ボタン A」として扱う。
  // Core2 / Fire の物理ボタン A もそのまま使える。
  const bool tapped = M5.Touch.isEnabled() && M5.Touch.getDetail().wasClicked();
  if (M5.BtnA.wasPressed() || tapped) {
    if (ws.isConnected()) {
      ws.sendTXT("{\"type\":\"button\",\"name\":\"A\"}");
      Serial.println(tapped ? "画面タップをボタン A として送信しました。" : "ボタン A を送信しました。");
    } else {
      Serial.println("未接続のためボタン A は送りません。");
    }
  }

  if (wav_ready) {
    wav_ready = false;
    if (!wav_discard) {
      playWav();
    }
    resetWav();
    if (ws.isConnected()) {
      ws.sendTXT("{\"type\":\"played\"}");
      setState(State::Listening);
    }
    return;
  }

  if (stateSendsMic(state) && mic_running && ws.isConnected()) {
    streamMicFrame();  // record() が枠の空きを待つので、ここが約 64 ms の周期になる
  } else {
    delay(5);
  }
}
