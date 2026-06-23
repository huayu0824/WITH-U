/*
 * sd_card.h — TF 卡 (SD SPI) 读写封装
 * 依赖: SD, SPI, ArduinoJson
 * 无卡时所有函数静默返回 false，不阻塞启动
 */

#ifndef SD_CARD_H
#define SD_CARD_H

#include <SD.h>
#include <SPI.h>
#include <ArduinoJson.h>

// ===== 全局状态 =====
extern bool sd_available;
static SPIClass* sdSPI = NULL;

// ===== 初始化 =====
bool sd_init() {
    if (sdSPI) {
        delete sdSPI;
        sdSPI = NULL;
    }
    sdSPI = new SPIClass(HSPI);
    sdSPI->begin(SD_SCK, SD_MISO, SD_MOSI, SD_CS);

    if (!SD.begin(SD_CS, *sdSPI, 20000000)) {  // 20MHz SPI
        Serial.println("[SD] 未检测到 TF 卡");
        sd_available = false;
        return false;
    }

    sd_available = true;
    uint64_t total = SD.totalBytes() / (1024 * 1024);
    uint64_t used = SD.usedBytes() / (1024 * 1024);
    Serial.printf("[SD] 已挂载: %llu MB / %llu MB\n", used, total);

    // 确保必要目录存在
    SD.mkdir("/system");
    SD.mkdir("/audio");
    SD.mkdir("/audio/commands");

    return true;
}

// ===== 基础文件操作 =====
bool sd_exists(const char* path) {
    if (!sd_available) return false;
    return SD.exists(path);
}

size_t sd_file_size(const char* path) {
    if (!sd_available) return 0;
    File f = SD.open(path, FILE_READ);
    if (!f) return 0;
    size_t s = f.size();
    f.close();
    return s;
}

// ===== 文本文件读写 =====
String sd_read_file(const char* path) {
    if (!sd_available) return "";
    File f = SD.open(path, FILE_READ);
    if (!f) {
        Serial.printf("[SD] 读失败: %s\n", path);
        return "";
    }
    String content;
    while (f.available()) {
        content += (char)f.read();
    }
    f.close();
    return content;
}

bool sd_write_file(const char* path, const String& content) {
    if (!sd_available) return false;
    // 原子写入: 先写 .tmp, 再 rename
    String tmp = String(path) + ".tmp";
    File f = SD.open(tmp.c_str(), FILE_WRITE);
    if (!f) {
        Serial.printf("[SD] 写失败(open): %s\n", path);
        return false;
    }
    size_t n = f.print(content);
    f.close();
    if (n == 0) {
        Serial.printf("[SD] 写失败(0 bytes): %s\n", path);
        SD.remove(tmp.c_str());
        return false;
    }
    SD.remove(path);
    if (!SD.rename(tmp.c_str(), path)) {
        Serial.printf("[SD] rename 失败: %s -> %s\n", tmp.c_str(), path);
        return false;
    }
    return true;
}

bool sd_append_file(const char* path, const String& content) {
    if (!sd_available) return false;
    File f = SD.open(path, FILE_APPEND);
    if (!f) return false;
    size_t n = f.print(content);
    f.close();
    return n > 0;
}

bool sd_delete_file(const char* path) {
    if (!sd_available) return false;
    return SD.remove(path);
}

// ===== 二进制文件读写 =====
uint8_t* sd_read_binary(const char* path, size_t* out_len) {
    *out_len = 0;
    if (!sd_available) return NULL;
    File f = SD.open(path, FILE_READ);
    if (!f) return NULL;
    size_t len = f.size();
    if (len == 0) { f.close(); return NULL; }
    uint8_t* buf = (uint8_t*)heap_caps_malloc(len, MALLOC_CAP_SPIRAM);
    if (!buf) { f.close(); return NULL; }
    size_t n = f.read(buf, len);
    f.close();
    if (n != len) {
        heap_caps_free(buf);
        return NULL;
    }
    *out_len = len;
    return buf;
}

bool sd_write_binary(const char* path, const uint8_t* data, size_t len) {
    if (!sd_available) return false;
    String tmp = String(path) + ".tmp";
    File f = SD.open(tmp.c_str(), FILE_WRITE);
    if (!f) return false;
    size_t n = f.write(data, len);
    f.close();
    if (n != len) {
        SD.remove(tmp.c_str());
        return false;
    }
    SD.remove(path);
    if (!SD.rename(tmp.c_str(), path)) return false;
    return true;
}

// ===== JSON 文件读写 =====
bool sd_read_json(const char* path, JsonDocument& doc) {
    if (!sd_available) return false;
    File f = SD.open(path, FILE_READ);
    if (!f) return false;
    DeserializationError err = deserializeJson(doc, f);
    f.close();
    if (err) {
        Serial.printf("[SD] JSON 解析失败(%s): %s\n", path, err.c_str());
        return false;
    }
    return true;
}

bool sd_write_json(const char* path, JsonDocument& doc) {
    if (!sd_available) return false;
    String tmp = String(path) + ".tmp";
    File f = SD.open(tmp.c_str(), FILE_WRITE);
    if (!f) return false;
    size_t n = serializeJson(doc, f);
    f.close();
    if (n == 0) {
        SD.remove(tmp.c_str());
        return false;
    }
    SD.remove(path);
    if (!SD.rename(tmp.c_str(), path)) return false;
    return true;
}

#endif // SD_CARD_H
