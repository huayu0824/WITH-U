/*
 * ota_update.h ? ESP32-S3 OTA Update Module
 *
 * Features:
 *   ? HTTP firmware download + flash via Update.h
 *   ? Version manifest parsing (JSON from server)
 *   ? Boot?time automatic check (with debounce to avoid loop storms)
 *   ? Double?click trigger for manual update
 *   ? Server?initiated update via WebSocket command ("OTA")
 *   ? Rollback on failure (ESP32 bootloader handles app0 ? app1)
 *
 * Partition layout (16MB flash):
 *   app0 / ota_0  ? factory / running
 *   app1 / ota_1  ? OTA target (swap on success)
 *
 * Dependencies:
 *   HTTPClient, Update, ArduinoJson (already in lib_deps)
 */
#ifndef OTA_UPDATE_H
#define OTA_UPDATE_H

#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <Update.h>
#include <ArduinoJson.h>
#include <esp_ota_ops.h>

// ??????????????????????????????????????????????????????????
//  CONFIG ? override in config.h before including this file
// ??????????????????????????????????????????????????????????
#ifndef FIRMWARE_VERSION
  #define FIRMWARE_VERSION "1.0.0"
#endif

#ifndef OTA_MANIFEST_URL
  #define OTA_MANIFEST_URL "http://129.204.166.180:8000/api/firmware/manifest"
#endif

#ifndef OTA_CHECK_INTERVAL_MS
  #define OTA_CHECK_INTERVAL_MS (6UL * 3600UL * 1000UL)   // every 6 hours
#endif

// ??????????????????????????????????????????????????????????
//  Manifest structure returned from server
// ??????????????????????????????????????????????????????????
struct OtaManifest {
  String version;
  String url;          // full URL to firmware .bin
  String md5;          // MD5 hex string (optional, "" to skip)
  size_t size;         // file size in bytes
  bool   valid;        // parsed successfully?
};

// ??????????????????????????????????????????????????????????
//  State (caller can read these)
// ??????????????????????????????????????????????????????????
static bool       _otaBusy       = false;    // download in progress
static bool       _otaPending    = false;    // new firmware ready to apply
static String     _otaNewVersion = "";
static unsigned long _lastOtaCheck = 0;

// ??????????????????????????????????????????????????????????
//  HTTP + JSON helpers
// ??????????????????????????????????????????????????????????

/// Fetch a URL into a String (simple GET, no streaming).
static bool _httpGetString(const String& url, String& out, int timeoutMs = 10000) {
  if (WiFi.status() != WL_CONNECTED) return false;
  HTTPClient http;
  http.setTimeout(timeoutMs);
  http.setConnectTimeout(5000);
  http.begin(url);
  int code = http.GET();
  if (code != 200) {
    Serial.printf("[OTA] HTTP %d from %s\n", code, url.c_str());
    http.end();
    return false;
  }
  out = http.getString();
  http.end();
  return true;
}

/// Parse JSON manifest from server.
static OtaManifest _parseManifest(const String& json) {
  OtaManifest m = {};
  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, json);
  if (err) {
    Serial.printf("[OTA] manifest JSON error: %s\n", err.c_str());
    return m;
  }
  m.version = doc["version"] | "";
  m.url     = doc["url"] | "";
  m.md5     = doc["md5"] | "";
  m.size    = doc["size"] | 0UL;
  m.valid   = !m.version.isEmpty() && !m.url.isEmpty() && m.size > 0;
  return m;
}

/// Return the current running partition label.
static String _runningPartition() {
  const esp_partition_t* p = esp_ota_get_running_partition();
  if (!p) return "?";
  return p->label;
}

// ??????????????????????????????????????????????????????????
//  Public API
// ??????????????????????????????????????????????????????????

/// Check server manifest and print status.  Returns true if an update is
/// available (caller should call otaApply() or notify the user).
bool otaCheck() {
  if (_otaBusy) {
    Serial.println("[OTA] busy, skipping check");
    return false;
  }
  _lastOtaCheck = millis();

  String json;
  if (!_httpGetString(OTA_MANIFEST_URL, json)) {
    Serial.println("[OTA] check failed (network)");
    return false;
  }

  OtaManifest m = _parseManifest(json);
  if (!m.valid) {
    Serial.println("[OTA] manifest invalid, skipping");
    return false;
  }

  Serial.printf("[OTA] server: v%s (%u bytes) | local: v%s | partition: %s\n",
                m.version.c_str(), m.size,
                FIRMWARE_VERSION, _runningPartition().c_str());

  if (m.version == FIRMWARE_VERSION) {
    Serial.println("[OTA] already up?to?date");
    return false;
  }

  Serial.printf("[OTA]  ?  new version %s available!\n", m.version.c_str());
  _otaPending    = true;
  _otaNewVersion = m.version;

  // Store metadata for otaApply()
  // We re-fetch the manifest at apply-time to avoid stale URL.
  // However, we also cache the key fields for convenience:
  return true;
}

/// Download and apply the firmware update.  Returns true on success.
/// On success the ESP32 will reboot into the new partition after ~1s.
bool otaApply() {
  if (_otaBusy) {
    Serial.println("[OTA] already applying");
    return false;
  }

  // Fetch manifest again to get fresh URL / MD5
  String json;
  if (!_httpGetString(OTA_MANIFEST_URL, json, 15000)) {
    Serial.println("[OTA] apply: cannot fetch manifest");
    return false;
  }
  OtaManifest m = _parseManifest(json);
  if (!m.valid) {
    Serial.println("[OTA] apply: manifest invalid");
    return false;
  }

  Serial.printf("[OTA] downloading %s ? %s ...\n",
                m.url.c_str(), _runningPartition().c_str());
  _otaBusy = true;

  HTTPClient http;
  http.setTimeout(30000);
  http.setConnectTimeout(10000);
  http.begin(m.url);

  int code = http.GET();
  if (code != 200) {
    Serial.printf("[OTA] download HTTP %d\n", code);
    http.end();
    _otaBusy = false;
    return false;
  }

  size_t totalLen = http.getSize();
  if (totalLen == 0) {
    Serial.println("[OTA] download: unknown size");
    http.end();
    _otaBusy = false;
    return false;
  }

  // ?? prepare OTA partition ??
  if (!Update.begin(totalLen, U_FLASH)) {
    Serial.printf("[OTA] Update.begin failed: %s\n", Update.errorString());
    http.end();
    _otaBusy = false;
    return false;
  }

  // ?? stream download ??
  WiFiClient* stream = http.getStreamPtr();
  uint8_t buf[4096];
  size_t written = 0;
  unsigned long t0 = millis();

  while (written < totalLen) {
    size_t avail = stream->available();
    if (avail == 0) {
      delay(5);
      continue;
    }
    size_t toRead = min(avail, sizeof(buf));
    size_t n = stream->readBytes(buf, toRead);
    if (n == 0) {
      Serial.println("[OTA] stream read returned 0, aborting");
      Update.abort();
      http.end();
      _otaBusy = false;
      return false;
    }
    size_t w = Update.write(buf, n);
    if (w != n) {
      Serial.printf("[OTA] write error at %u/%u: %s\n",
                    written, totalLen, Update.errorString());
      Update.abort();
      http.end();
      _otaBusy = false;
      return false;
    }
    written += w;

    // Progress every 10%
    static int lastPct = -1;
    int pct = (written * 100) / totalLen;
    if (pct / 10 != lastPct / 10) {
      lastPct = pct;
      Serial.printf("[OTA] %d%% (%u / %u bytes, %us)\n",
                    pct, written, totalLen, (millis() - t0) / 1000);
    }
  }

  // ?? verify (MD5 check if provided) ??
  if (!m.md5.isEmpty()) {
    Update.setMD5(m.md5.c_str());
  }
  if (!Update.end()) {
    Serial.printf("[OTA] Update.end failed: %s\n", Update.errorString());
    http.end();
    _otaBusy = false;
    return false;
  }

  http.end();

  if (!Update.isFinished()) {
    Serial.println("[OTA] Update not finished (should not happen)");
    _otaBusy = false;
    return false;
  }

  Serial.printf("[OTA]  ? success! %u bytes in %us. rebooting...\n",
                written, (millis() - t0) / 1000);
  Serial.flush();

  _otaPending    = false;
  _otaBusy       = false;
  _otaNewVersion = "";

  delay(500);
  ESP.restart();
  return true;  // unreachable, but clean
}

/// Should we check for updates?  (boot and periodic)
bool otaShouldCheck() {
  if (_otaBusy) return false;
  // Check on first call (boot) and every OTA_CHECK_INTERVAL_MS thereafter
  if (_lastOtaCheck == 0) return true;
  if (millis() - _lastOtaCheck >= OTA_CHECK_INTERVAL_MS) return true;
  return false;
}

/// Non?blocking tick ? call from loop().  Handles periodic checks
/// automatically.  If an update is found it is applied immediately.
void otaTick() {
  if (!otaShouldCheck()) return;
  if (!otaCheck()) return;
  // Update available ? apply now (blocking, ~30s download)
  otaApply();
}

/// Is an OTA operation in progress?
bool otaIsBusy() { return _otaBusy; }

/// Is a new version pending (checked but not yet applied)?
bool otaIsPending() { return _otaPending; }
String otaPendingVersion() { return _otaNewVersion; }

/// Reset the periodic timer so the next check happens after the full interval.
void otaResetTimer() { _lastOtaCheck = millis(); }

#endif // OTA_UPDATE_H
