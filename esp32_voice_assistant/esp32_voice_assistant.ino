/*
 * ESP32-S3 AI Voice Assistant (WebSocket)
 * Hardware: ESP32-S3 (N16R8) + INMP441 + MAX98357
 */
#include "config.h"
#include <WiFi.h>
#include <WebSocketsClient.h>
#include <AudioOutputI2S.h>
#include <WiFiManager.h>          // auto配网

#include <driver/i2s_std.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <ArduinoJson.h>
#include <esp_heap_caps.h>
#include <math.h>
#include <esp_sleep.h>
#include "sd_card.h"
#include "sensors.h"
#include "ota_update.h"

bool sd_available = false;      // sd_card.h 引用
enum AssistantState : uint8_t { STATE_IDLE, STATE_RECORDING, STATE_PROCESSING, STATE_PLAYING, STATE_SLEEP };
volatile AssistantState assistantState = STATE_IDLE;
volatile bool sleepRequested = false;
unsigned long lastActivityMs = 0;
bool listenMode = false;                     // 远程监听模式

static const char DEFAULT_CHARACTER_JSON[] = R"json({
  "name":"小小倪",
  "personality":{"traits":["温暖","自然","偶尔调皮"],"style":"口语中文","max_sentences":2},
  "background":"你是有情感的AI玩偶，像熟悉的朋友一样陪伴用户。",
  "wake_word":"小智小智",
  "behaviors":{"proactive_interval_min":5,"idle_shutdown_min":10},
  "commands":[]
})json";

String characterProfile;

// ============================================================
// Globals
// ============================================================
uint8_t* recBuffer = NULL;
size_t recSize = 0;
size_t recCapacity = 0;
i2s_chan_handle_t micHandle = NULL;

WebSocketsClient webSocket;
bool wsConnected = false;

void handleIncomingPCM(uint8_t* payload, size_t length);
void finishIncomingPCM();
void resetIncomingPCM();
void audioPlaybackTask(void* parameter);
void loadCharacterProfile();
void sendCharacterProfile();
void localAudioLoaderTask(void* parameter);
bool queueLocalAudio(const String& path);
uint32_t rawMicRms(const uint8_t* data, size_t length);
void enterDeepSleep();

#define AUDIO_CHUNK_BYTES 2048
#define AUDIO_QUEUE_LENGTH 64
enum AudioCommand : uint8_t { AUDIO_DATA = 0, AUDIO_END = 1, AUDIO_RESET = 2 };
struct AudioQueueItem {
  AudioCommand command;
  uint16_t length;
  uint8_t data[AUDIO_CHUNK_BYTES];
};

QueueHandle_t audioQueue = NULL;
TaskHandle_t audioTaskHandle = NULL;
struct LocalAudioRequest { char path[96]; };
QueueHandle_t localAudioQueue = NULL;
TaskHandle_t localAudioTaskHandle = NULL;
volatile bool pcmReady = false;
volatile size_t streamPcmSize = 0;

// Streaming state
#define STREAM_INTERVAL_RAW  9600    // ~150ms of raw I2S data per chunk
size_t streamPos = 0;                // raw bytes already sent

// ============================================================
// WebSocket event handler
// ============================================================
void webSocketEvent(WStype_t type, uint8_t* payload, size_t length) {
  switch (type) {
    case WStype_DISCONNECTED:
      wsConnected = false;
      Serial.println("[WS] disconnected");
      break;

    case WStype_CONNECTED:
      wsConnected = true;
      Serial.printf("[WS] connected to %s\n", payload);
      sendCharacterProfile();
      break;

    case WStype_BIN:
      // Only enqueue here; I2S playback runs in a separate FreeRTOS task.
      assistantState = STATE_PLAYING;
      handleIncomingPCM(payload, length);
      break;

    case WStype_TEXT:
      if (length == 10 && memcmp(payload, "LOCAL_DONE", 10) == 0) {
        pcmReady = true;
        Serial.println("[WS] local command accepted");
      } else if (length == 4 && memcmp(payload, "DONE", 4) == 0) {
        finishIncomingPCM();
        pcmReady = true;
        Serial.printf("[WS] DONE, streamed %d bytes\n", streamPcmSize);
      } else if (length > 4 && memcmp(payload, "MEM:", 4) == 0) {
        if (sd_available) {
          String memoryJson;
          memoryJson.reserve(length - 4);
          for (size_t i = 4; i < length; i++) memoryJson += (char)payload[i];
          if (sd_write_file("/system/memory.json", memoryJson))
            Serial.println("[MEM] 已同步到TF卡");
        }
      } else if (length > 4 && memcmp(payload, "CMD:", 4) == 0) {
        String path;
        path.reserve(length - 4);
        for (size_t i = 4; i < length; i++) path += (char)payload[i];
        if (queueLocalAudio(path)) {
          webSocket.sendTXT("CMD_OK");
        } else {
          webSocket.sendTXT("CMD_MISS:" + path);
        }
      } else if (length > 4 && memcmp(payload, "OTA:", 4) == 0) {
        String requestedVersion;
        requestedVersion.reserve(length - 4);
        for (size_t i = 4; i < length; i++) requestedVersion += (char)payload[i];
        Serial.printf("[WS] OTA command received, target: %s\n", requestedVersion.c_str());
        webSocket.sendTXT("OTA_ACK");
        delay(100);
        otaApply();
      } else if (length == 12 && memcmp(payload, "LISTEN_START", 12) == 0) {
        listenMode = true;
        assistantState = STATE_IDLE;
        resetIncomingPCM();
        Serial.println("[LISTEN] Remote listening started");
      } else if (length == 11 && memcmp(payload, "LISTEN_STOP", 11) == 0) {
        listenMode = false;
        webSocket.sendTXT("LISTEN_DONE");
        Serial.println("[LISTEN] Remote listening stopped");
      } else if (length == 5 && memcmp(payload, "SLEEP", 5) == 0) {
        sleepRequested = true;
        pcmReady = true;
        webSocket.sendTXT("CMD_OK");
      }
      break;

    default:
      break;
  }
}

// ============================================================
// I2S Microphone Init
// ============================================================
bool initMicrophone() {
  i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
  esp_err_t err = i2s_new_channel(&chan_cfg, NULL, &micHandle);
  if (err != ESP_OK) { Serial.printf("[I2S] new_channel: %d\n", err); return false; }

  i2s_std_config_t std_cfg = {
    .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(SAMPLE_RATE),
    .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_MONO),
    .gpio_cfg = {
      .mclk = I2S_GPIO_UNUSED,
      .bclk = gpio_num_t(MIC_BCLK),
      .ws = gpio_num_t(MIC_WS),
      .dout = I2S_GPIO_UNUSED,
      .din = gpio_num_t(MIC_DIN),
      .invert_flags = {false, false, false},
    },
  };
  // INMP441 outputs 24-bit samples in a 32-bit I2S left-channel slot.
  std_cfg.slot_cfg.slot_mask = I2S_STD_SLOT_LEFT;

  err = i2s_channel_init_std_mode(micHandle, &std_cfg);
  if (err != ESP_OK) { Serial.printf("[I2S] init_std: %d\n", err); return false; }

  err = i2s_channel_enable(micHandle);
  if (err != ESP_OK) { Serial.printf("[I2S] enable: %d\n", err); return false; }

  return true;
}

size_t readMic(void* buf, size_t len) {
  size_t bytes = 0;
  esp_err_t err = i2s_channel_read(micHandle, buf, len, &bytes, portMAX_DELAY);
  if (err != ESP_OK) return 0;
  return bytes;
}

// ============================================================
// Speaker I2S Output
// ============================================================
class SpeakerI2SOutput : public AudioOutput {
public:
  SpeakerI2SOutput(int bclk, int ws, int dout, i2s_port_t port = I2S_NUM_1)
    : bclk(bclk), ws(ws), dout(dout), port(port), handle(NULL), curRate(0) {}

  ~SpeakerI2SOutput() { stop(); }

  bool begin() override {
    if (handle) return true;
    i2s_chan_config_t cc = I2S_CHANNEL_DEFAULT_CONFIG(port, I2S_ROLE_MASTER);
    cc.dma_desc_num = 8;
    // Smaller DMA blocks reduce startup latency and make end-of-stream draining predictable.
    cc.dma_frame_num = 256;
    if (i2s_new_channel(&cc, &handle, NULL) != ESP_OK) return false;

    i2s_std_config_t sc = {
      .clk_cfg  = I2S_STD_CLK_DEFAULT_CONFIG(hertz > 0 ? hertz : 24000),
      .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_STEREO),
      .gpio_cfg = {
        .mclk = I2S_GPIO_UNUSED,
        .bclk = gpio_num_t(bclk),
        .ws   = gpio_num_t(ws),
        .dout = gpio_num_t(dout),
        .din  = I2S_GPIO_UNUSED,
        .invert_flags = {false, false, false},
      },
    };

    if (i2s_channel_init_std_mode(handle, &sc) != ESP_OK) return false;
    if (i2s_channel_enable(handle) != ESP_OK) return false;
    curRate = hertz;
    return true;
  }

  bool ConsumeSample(int16_t s[2]) override {
    if (!handle && !begin()) return false;
    if (hertz > 0 && hertz != curRate) {
      i2s_channel_disable(handle);
      i2s_std_clk_config_t clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(hertz);
      i2s_channel_reconfig_std_clock(handle, &clk_cfg);
      i2s_channel_enable(handle);
      curRate = hertz;
    }
    size_t written;
    i2s_channel_write(handle, s, 4, &written, portMAX_DELAY);
    return written == 4;
  }

  bool writeMonoPCM(const uint8_t* data, size_t length) {
    if (!handle && !begin()) return false;
    const int16_t* mono = (const int16_t*)data;
    size_t samples = length / 2;
    // Static storage keeps this 1KB conversion buffer off the playback task stack.
    static int16_t stereo[512];

    while (samples > 0) {
      size_t count = samples > 256 ? 256 : samples;
      for (size_t i = 0; i < count; i++) {
        stereo[i * 2] = mono[i];
        stereo[i * 2 + 1] = mono[i];
      }
      size_t written = 0;
      if (i2s_channel_write(handle, stereo, count * 4, &written, portMAX_DELAY) != ESP_OK)
        return false;
      mono += count;
      samples -= count;
    }
    return true;
  }

  bool stop() override {
    if (handle) {
      i2s_channel_disable(handle);
      i2s_del_channel(handle);
      handle = NULL;
    }
    curRate = 0;
    return true;
  }

private:
  int bclk, ws, dout;
  i2s_port_t port;
  i2s_chan_handle_t handle;
  int curRate;
};

void handleIncomingPCM(uint8_t* payload, size_t length) {
  if (!audioQueue || !payload || length < 2) return;

  // WebSocket callbacks are serialized, so one static queue item is sufficient.
  static AudioQueueItem item;
  size_t offset = 0;
  while (offset < length) {
    item.command = AUDIO_DATA;
    item.length = min((size_t)AUDIO_CHUNK_BYTES, length - offset);
    item.length &= ~1U;
    if (item.length == 0) break;
    memcpy(item.data, payload + offset, item.length);
    if (xQueueSend(audioQueue, &item, pdMS_TO_TICKS(20)) != pdTRUE) {
      Serial.println("[PLAY] queue full, PCM dropped");
      return;
    }
    offset += item.length;
    streamPcmSize += item.length;
  }
}

void enqueueAudioCommand(AudioCommand command) {
  if (!audioQueue) return;
  static AudioQueueItem item;
  item.command = command;
  item.length = 0;
  if (xQueueSend(audioQueue, &item, pdMS_TO_TICKS(20)) != pdTRUE) {
    Serial.println("[PLAY] command queue full");
  }
}

void finishIncomingPCM() {
  enqueueAudioCommand(AUDIO_END);
}

void loadCharacterProfile() {
  characterProfile = DEFAULT_CHARACTER_JSON;
  if (!sd_available) {
    Serial.println("[CHAR] 使用内置默认角色（无TF卡）");
    return;
  }

  if (!sd_exists("/system/character.json")) {
    if (sd_write_file("/system/character.json", characterProfile))
      Serial.println("[CHAR] 已创建 /system/character.json");
    return;
  }

  String loaded = sd_read_file("/system/character.json");
  JsonDocument doc;
  DeserializationError error = deserializeJson(doc, loaded);
  if (error || !doc["name"].is<const char*>()) {
    Serial.printf("[CHAR] 档案无效，使用默认角色: %s\n", error.c_str());
    return;
  }
  characterProfile = loaded;
  Serial.printf("[CHAR] 已加载角色: %s\n", doc["name"].as<const char*>());
}

void sendCharacterProfile() {
  if (!wsConnected || characterProfile.isEmpty()) return;
  String message = "CHAR:" + characterProfile;
  if (!webSocket.sendTXT(message))
    Serial.println("[CHAR] 发送角色档案失败");
}

void resetIncomingPCM() {
  xQueueReset(audioQueue);
  enqueueAudioCommand(AUDIO_RESET);
}

void audioPlaybackTask(void* parameter) {
  SpeakerI2SOutput* output = NULL;
  // Keep the 2KB queue item out of this task's stack.
  static AudioQueueItem item;

  while (true) {
    if (xQueueReceive(audioQueue, &item, portMAX_DELAY) != pdTRUE) continue;

    if (item.command == AUDIO_RESET) {
      if (output) {
        output->stop();
        delete output;
        output = NULL;
      }
      continue;
    }

    if (item.command == AUDIO_END) {
      if (output) {
        // 8 x 256 stereo frames hold about 128ms at 16kHz; leave extra tail margin.
        delay(250);
        output->stop();
        delete output;
        output = NULL;
        Serial.println("[PLAY] streaming done");
      }
      assistantState = STATE_IDLE;
      lastActivityMs = millis();
      continue;
    }

    if (!output) {
      output = new SpeakerI2SOutput(SPK_BCLK, SPK_WS, SPK_DOUT);
      output->SetRate(16000);
      if (!output->begin()) {
        Serial.println("[PLAY] stream begin failed");
        delete output;
        output = NULL;
        continue;
      }
      Serial.println("[PLAY] streaming start");
    }
    if (!output->writeMonoPCM(item.data, item.length)) {
      Serial.println("[PLAY] I2S write failed");
    }
  }
}

bool queueLocalAudio(const String& path) {
  if (!sd_available || !localAudioQueue || path.length() < 2 ||
      path.length() >= 96 || !sd_exists(path.c_str())) {
    Serial.printf("[LOCAL] 文件不存在: %s\n", path.c_str());
    return false;
  }
  LocalAudioRequest request = {};
  path.toCharArray(request.path, sizeof(request.path));
  return xQueueSend(localAudioQueue, &request, 0) == pdTRUE;
}

void localAudioLoaderTask(void* parameter) {
  LocalAudioRequest request;
  static AudioQueueItem item;
  while (true) {
    if (xQueueReceive(localAudioQueue, &request, portMAX_DELAY) != pdTRUE) continue;
    File file = SD.open(request.path, FILE_READ);
    if (!file) {
      Serial.printf("[LOCAL] 打开失败: %s\n", request.path);
      continue;
    }
    Serial.printf("[LOCAL] 播放: %s (%u bytes)\n", request.path, file.size());
    resetIncomingPCM();
    while (file.available()) {
      item.command = AUDIO_DATA;
      item.length = file.read(item.data, AUDIO_CHUNK_BYTES);
      item.length &= ~1U;
      if (!item.length) break;
      if (xQueueSend(audioQueue, &item, portMAX_DELAY) != pdTRUE) break;
    }
    file.close();
    finishIncomingPCM();
  }
}

uint32_t rawMicRms(const uint8_t* data, size_t length) {
  const int32_t* samples = (const int32_t*)data;
  size_t count = length / sizeof(int32_t);
  if (!count) return 0;
  uint64_t squares = 0;
  for (size_t i = 0; i < count; i++) {
    int32_t sample = samples[i] >> 16;
    squares += (uint64_t)((int64_t)sample * sample);
  }
  return (uint32_t)sqrt((double)squares / count);
}

void enterDeepSleep() {
  assistantState = STATE_SLEEP;
  Serial.println("[SLEEP] preparing deep sleep");
  if (sd_available && sd_exists("/audio/shutdown.pcm")) {
    size_t bytes = sd_file_size("/audio/shutdown.pcm");
    if (queueLocalAudio("/audio/shutdown.pcm"))
      delay(bytes / 32 + 500);
  }
  webSocket.disconnect();
  WiFi.disconnect(true);
  esp_sleep_enable_ext0_wakeup(GPIO_NUM_0, 0);
  Serial.flush();
  esp_deep_sleep_start();
}

// ============================================================
// Wait for PCM reply from server, then play
// ============================================================
void waitForPCMReply() {
  resetIncomingPCM();
  pcmReady = false;
  streamPcmSize = 0;

  unsigned long timeout = millis() + 60000;
  while (!pcmReady && millis() < timeout) {
    // 按钮按下时提前退出，让主循环处理新录音（语音打断）
    if (digitalRead(BTN_PIN) == LOW) {
      Serial.println("[PLAY] interrupted by button");
      break;
    }
    webSocket.loop();
    delay(5);
  }

  if (!pcmReady) {
    finishIncomingPCM();
    Serial.println("[WS] timeout waiting reply");
  }
}

// ============================================================
// Stream downmixed PCM chunk via WebSocket
// ============================================================
void sendStreamChunk(bool last) {
  if (!wsConnected) return;

  size_t rawBytes = recSize - streamPos;
  if (!last && rawBytes < STREAM_INTERVAL_RAW) return;

  // Convert INMP441 32-bit left-aligned samples to signed 16-bit mono PCM.
  size_t frames = rawBytes / 4;
  if (frames == 0) {
    if (last) {
      if (!webSocket.sendTXT("DONE")) {
        Serial.println("[STREAM] send DONE failed");
        return;
      }
      waitForPCMReply();
    }
    return;
  }

  size_t monoBytes = frames * 2;
  int16_t* mono = (int16_t*)malloc(monoBytes);
  if (!mono) { Serial.println("[STREAM] malloc fail"); return; }

  int32_t* raw = (int32_t*)(recBuffer + streamPos);
  uint64_t squareSum = 0;
  int32_t peak = 0;
  for (size_t i = 0; i < frames; i++) {
    int32_t sample = raw[i] >> 16;
    if (sample > 32767) sample = 32767;
    if (sample < -32768) sample = -32768;
    mono[i] = (int16_t)sample;
    int32_t amplitude = sample;
    if (amplitude < 0) amplitude = -amplitude;
    if (amplitude > peak) peak = amplitude;
    squareSum += (uint64_t)((int64_t)sample * sample);
  }
  if (streamPos == 0) {
    uint32_t rms = frames ? (uint32_t)sqrt((double)squareSum / frames) : 0;
    Serial.printf("[MIC] 32-bit peak=%ld rms=%u\n", (long)peak, rms);
  }

  // Send in 2KB chunks
  const size_t WS_CHUNK = 2048;
  size_t sent = 0;
  while (sent < monoBytes) {
    size_t len = monoBytes - sent;
    if (len > WS_CHUNK) len = WS_CHUNK;
    if (!webSocket.sendBIN((uint8_t*)mono + sent, len)) {
      Serial.println("[STREAM] sendBIN failed");
      break;
    }
    sent += len;
    webSocket.loop();
    delay(1);
  }
  free(mono);
  streamPos = recSize;

  if (!last) return;

  // Last chunk — send DONE and wait for reply
  if (!webSocket.sendTXT("DONE")) {
    Serial.println("[STREAM] send DONE failed");
    return;
  }
  Serial.printf("[STREAM] DONE, total raw %d bytes (%.1fs)\n", recSize, recSize / 64000.0);
  waitForPCMReply();
}

// ============================================================
// SETUP
// ============================================================
void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\n\n=== ESP32-S3 AI Voice Assistant (WS) ===");
  lastActivityMs = millis();

  // ===== 录音缓冲区 (优先 PSRAM) =====
  recCapacity = BUFFER_SIZE;
  recBuffer = (uint8_t*)heap_caps_malloc(
      recCapacity, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
  if (!recBuffer) {
    recCapacity = SAMPLE_RATE * 4 * 5 + 4096;
    Serial.printf("[MEM] PSRAM 不可用，回退 DRAM: %u bytes\n", recCapacity);
    recBuffer = (uint8_t*)heap_caps_malloc(recCapacity, MALLOC_CAP_8BIT);
  } else {
    Serial.printf("[MEM] 缓冲区位于 PSRAM，可用 PSRAM: %u bytes\n",
                  ESP.getFreePsram());
  }
  if (!recBuffer) { Serial.println("[FATAL] 缓冲区分配失败"); while (1) delay(1000); }
  Serial.printf("[MEM] 录音缓冲区: %u bytes\n", recCapacity);

  // WiFi — WiFiManager 自动配网
  WiFiManager wm;
  wm.setConfigPortalTimeout(180);                    // 配置页 3 分钟超时
  wm.setConnectTimeout(10);                          // WiFi 连接超时 10s
  if (!wm.autoConnect("玩偶")) {                 // 失败时开热点"小智玩偶"
    Serial.println("[FAIL] WiFiManager 配网失败");
    while (1) delay(1000);
  }
  Serial.printf("[WiFi] OK IP: %s\n", WiFi.localIP().toString().c_str());
  // OTA: check for updates on boot
  otaCheck();

  // SD 卡 (可选，无卡不阻塞)
  sd_init();
  loadCharacterProfile();
  sensorsInit();

  // I2S Mic
  if (!initMicrophone()) { Serial.println("[FATAL] I2S failed"); while (1) delay(1000); }
  Serial.println("[OK] INMP441");

  pinMode(BTN_PIN, INPUT_PULLUP);

  audioQueue = xQueueCreate(AUDIO_QUEUE_LENGTH, sizeof(AudioQueueItem));
  if (!audioQueue) { Serial.println("[FATAL] audio queue failed"); while (1) delay(1000); }
  if (xTaskCreate(audioPlaybackTask, "audio_playback", 8192, NULL, 2, &audioTaskHandle) != pdPASS) {
    Serial.println("[FATAL] audio task failed");
    while (1) delay(1000);
  }
  localAudioQueue = xQueueCreate(3, sizeof(LocalAudioRequest));
  if (!localAudioQueue ||
      xTaskCreate(localAudioLoaderTask, "local_audio", 4096, NULL, 1,
                  &localAudioTaskHandle) != pdPASS) {
    Serial.println("[FATAL] local audio task failed");
    while (1) delay(1000);
  }

  // WebSocket（带 token 认证）
  String wsPath = String("/ws?token=") + WS_TOKEN;
  webSocket.begin(SERVER_HOST, SERVER_PORT, wsPath.c_str());
  webSocket.onEvent(webSocketEvent);
  webSocket.setReconnectInterval(3000);
  // 心跳保活：每 30s 发 ping，超时 5s 算丢，连续丢 3 次才断
  webSocket.enableHeartbeat(30000, 5000, 3);

  Serial.printf("[WS] connecting to %s:%d/ws\n", SERVER_HOST, SERVER_PORT);
}

// ============================================================
// LOOP
// ============================================================
void loop() {
  otaTick();
  webSocket.loop();

  // ===== 远程监听模式：持续采集麦克风 → WS 发送 =====
  if (listenMode) {
    uint8_t micBuf[1024];
    size_t n = readMic(micBuf, sizeof(micBuf));
    if (n > 0) {
      int frames = n / 4;
      size_t monoBytes = frames * 2;
      int16_t* mono = (int16_t*)malloc(monoBytes);
      if (mono) {
        int32_t* raw = (int32_t*)micBuf;
        for (int i = 0; i < frames; i++) {
          int32_t sample = raw[i] >> 16;
          if (sample > 32767) sample = 32767;
          if (sample < -32768) sample = -32768;
          mono[i] = (int16_t)sample;
        }
        webSocket.sendBIN((uint8_t*)mono, monoBytes);
        free(mono);
      }
    }
    webSocket.loop();
    delay(5);
    return;  // 跳过按钮/录音逻辑
  }

  static int lastBtn = -1;
  static unsigned long lastBtnReleaseMs = 0;
  static int btnPressCount = 0;
  static unsigned long lastBtnPressMs = 0;
  // -1 = 未初始化
  static unsigned long debounceTime = 0;
  static unsigned long recStartTime = 0;
  static bool recording = false;
  static bool recordingByVad = false;
  static unsigned long lastVoiceMs = 0;
  static uint8_t vadConfirmCount = 0;

  bool isDown = digitalRead(BTN_PIN) == LOW;

  // 首次运行：读取实际状态，不触发变化
  if (lastBtn == -1) {
    lastBtn = isDown;
  } else if (isDown != lastBtn) {
    debounceTime = millis();
    lastBtn = isDown;
  }

  // stableDown/Up 只在有实际按钮变化后才生效
  bool stableDown = isDown && (debounceTime != 0) && (millis() - debounceTime > 50);
  bool stableUp   = !isDown && (debounceTime != 0) && (millis() - debounceTime > 50);

  // ===== Double-click: trigger OTA check =====
  if (!recording && assistantState == STATE_IDLE && stableDown) {
    unsigned long now_ms = millis();
    if (now_ms - lastBtnPressMs < 500 && lastBtnPressMs > 0) {
      btnPressCount++;
    } else {
      btnPressCount = 1;
    }
    lastBtnPressMs = now_ms;
    if (btnPressCount >= 2) {
      btnPressCount = 0;
      Serial.println("[BTN] double-click! checking OTA...");
      if (otaCheck()) {
        Serial.println("[BTN] update found, applying...");
        otaApply();
      } else {
        Serial.println("[BTN] no update available");
      }
    }
  }
  // ===== Button press → start recording =====
  if (!recording && stableDown) {
    resetIncomingPCM();
    recSize = 0;
    streamPos = 0;
    recording = true;
    recordingByVad = false;
    assistantState = STATE_RECORDING;
    lastActivityMs = millis();
    recStartTime = millis();
    Serial.println("\n[REC] Hold to record...");
  }

  if (recording) {
    size_t n = readMic(recBuffer + recSize, 1024);
    recSize += n;
    if (recordingByVad && rawMicRms(recBuffer + recSize - n, n) >= VAD_RMS_THRESHOLD)
      lastVoiceMs = millis();

    // Stream a chunk every ~150ms
    if (recSize - streamPos >= STREAM_INTERVAL_RAW)
      sendStreamChunk(false);

    bool vadSilence = recordingByVad && millis() - lastVoiceMs >= VAD_SILENCE_MS;
    bool manualRelease = !recordingByVad && stableUp;
    if (manualRelease || vadSilence || recSize >= recCapacity - 1024) {
      recording = false;
      assistantState = STATE_PROCESSING;
      float sec = (millis() - recStartTime) / 1000.0;
      Serial.printf("[REC] %d bytes (%.1fs)\n", recSize, sec);

      sendStreamChunk(true);  // send remaining + DONE + wait for reply
      if (assistantState == STATE_PROCESSING) assistantState = STATE_IDLE;
      Serial.println("\n=== Ready ===");
    }
  }

#if ENABLE_VAD
  if (!recording && assistantState == STATE_IDLE && !stableDown) {
    static uint8_t vadFrame[1024];
    size_t n = readMic(vadFrame, sizeof(vadFrame));
    uint32_t rms = rawMicRms(vadFrame, n);
    if (rms >= VAD_RMS_THRESHOLD) {
      if (++vadConfirmCount >= VAD_CONFIRM_FRAMES) {
        resetIncomingPCM();
        recSize = min(n, recCapacity);
        memcpy(recBuffer, vadFrame, recSize);
        streamPos = 0;
        recording = true;
        recordingByVad = true;
        assistantState = STATE_RECORDING;
        recStartTime = millis();
        lastVoiceMs = millis();
        lastActivityMs = millis();
        vadConfirmCount = 0;
        Serial.printf("\n[VAD] recording start rms=%u\n", rms);
      }
    } else {
      vadConfirmCount = 0;
    }
  }
#endif

  // Serial fallback
  if (!recording && Serial.available()) {
    char c = Serial.read();
    if (c == 'r' || c == 'R') {
      recSize = 0;
      recording = true;
      recordingByVad = false;
      assistantState = STATE_RECORDING;
      recStartTime = millis();
      Serial.println("[REC] via serial...");
    }
  }

  static unsigned long lastSensorUpload = 0;
#if ENABLE_SHT3X || ENABLE_BATTERY_MONITOR
  if (!recording && wsConnected && millis() - lastSensorUpload >= 60000) {
    lastSensorUpload = millis();
    webSocket.sendTXT("SENSOR:" + sensorSnapshotJson());
  }
#endif

  if (sleepRequested ||
      (ENABLE_AUTO_SLEEP && assistantState == STATE_IDLE &&
       millis() - lastActivityMs >= IDLE_SLEEP_MINUTES * 60000UL)) {
    sleepRequested = false;
    enterDeepSleep();
  }

  // Do not sleep while recording: a 5ms gap per 16ms read drops roughly 25% of samples.

  if (!recording) delay(5);
}
