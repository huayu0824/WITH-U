// config.h - ESP32-S3 ??????

#ifndef CONFIG_H
#define CONFIG_H

// ===== ????? OTA =====
#define FIRMWARE_VERSION       "1.1.0"
#define OTA_CHECK_INTERVAL_MS  (6UL * 3600UL * 1000UL)   // ? 6 ??????
#define OTA_MANIFEST_URL       "http://129.204.166.180:8000/api/firmware/manifest"

// ===== ?????? =====
#define ENV_LOCAL
// #define ENV_CLOUD

#ifdef ENV_CLOUD
  const char* SERVER_HOST = "your-domain.com";
#else
  const char* SERVER_HOST = "129.204.166.180";
#endif

const int   SERVER_PORT = 8000;

// ===== ?????? server/config.py ??? =====
const char* WS_TOKEN = "doll-token-001";

// ===== I2S ?? =====
// INMP441 ???
#define MIC_BCLK   5
#define MIC_WS     4
#define MIC_DIN    6

// MAX98357A ??
#define SPK_BCLK   15
#define SPK_WS     16
#define SPK_DOUT   7

// ===== ?? =====
#define BTN_PIN    0   // IO0?????
// ===== ???? =====
#define SAMPLE_RATE     16000
#define RECORD_SECONDS  30
#define BUFFER_SIZE     (SAMPLE_RATE * 4 * RECORD_SECONDS + 4096)

// ===== TF ? (SD SPI) =====
#define SD_CS    10
#define SD_MOSI  11
#define SD_SCK   12
#define SD_MISO  13

// ===== ???????????? 1? =====
#define ENABLE_VAD              0
#define ENABLE_AUTO_SLEEP       0
#define ENABLE_SHT3X            1
#define ENABLE_BATTERY_MONITOR  0

// 32-bit INMP441 ? 16-bit ???????????? RMS ????
#define VAD_RMS_THRESHOLD       600
#define VAD_CONFIRM_FRAMES      3
#define VAD_SILENCE_MS          1200

// SHT3X I2C ?????????
#define SENSOR_SDA              41
#define SENSOR_SCL              42
#define SHT3X_ADDRESS           0x44
#define BATTERY_ADC_PIN         1
#define BATTERY_DIVIDER_RATIO   2.0f

#define IDLE_SLEEP_MINUTES      10

#endif
