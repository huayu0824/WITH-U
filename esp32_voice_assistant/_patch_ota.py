p = "D:\\??\\esp32_voice_assistant\\esp32_voice_assistant.ino"
content = open(p, "r", encoding="utf-8").read()
print(f"Read {len(content)} chars OK")

# 1. Add ota_update.h include after sensors.h
old = '#include "sensors.h"'
new = '#include "sensors.h"\n#include "ota_update.h"'
assert old in content, "sensors.h include not found!"
content = content.replace(old, new, 1)
print("Patched include")

# 2. Add OTA check after WiFi connects
old = 'Serial.printf("[WiFi] OK IP: %s\\n", WiFi.localIP().toString().c_str());'
assert old in content, "WiFi OK line not found!"
new = old + "\n  // OTA: check for updates on boot\n  otaCheck();"
content = content.replace(old, new, 1)
print("Patched WiFi -> OTA check")

# 3. Add otaTick at top of loop
old = "void loop() {\n  webSocket.loop();"
assert old in content, "loop start not found!"
new = "void loop() {\n  otaTick();\n  webSocket.loop();"
content = content.replace(old, new, 1)
print("Patched loop() -> otaTick")

# 4. Add OTA WS command handler before LISTEN_START
old = "} else if (length == 12 && memcmp(payload, \"LISTEN_START\", 12) == 0) {"
assert old in content, "LISTEN_START not found!"
ota_handler = """      } else if (length > 4 && memcmp(payload, "OTA:", 4) == 0) {
        // Server-initiated OTA: OTA:version
        String requestedVersion;
        requestedVersion.reserve(length - 4);
        for (size_t i = 4; i < length; i++) requestedVersion += (char)payload[i];
        Serial.printf("[WS] OTA command received, target: %s\\n", requestedVersion.c_str());
        webSocket.sendTXT("OTA_ACK");
        delay(100);
        otaApply();
      """
new = ota_handler + old
content = content.replace(old, new, 1)
print("Patched WS OTA handler")

# 5. Add double-click variables after lastBtn
old = "static int lastBtn = -1;             // -1 = \xe6\x9c\xaa\xe5\x88\x9d\xe5\xa7\x8b\xe5\x8c\x96"
new = old + "\n  static unsigned long lastBtnReleaseMs = 0;\n  static int btnPressCount = 0;\n  static unsigned long lastBtnPressMs = 0;"
assert old in content, "lastBtn not found!"
content = content.replace(old, new, 1)
print("Patched double-click variables")

# 6. Add double-click detection before the existing recording button check
old = "  if (!recording && stableDown) {"
dbl_click = """  // ===== Double-click: trigger OTA check =====
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

"""
content = content.replace(old, dbl_click + old, 1)
print("Patched double-click detection")

open(p, "w", encoding="utf-8").write(content)
print("Write OK")
