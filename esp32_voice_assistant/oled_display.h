/*
 * oled_display.h — LCD9648 (ST7565 128×64) 表情显示模块
 *
 * 硬件：LCD9648 / ST7565 128×64 SPI
 * 使用 U8G2 库驱动
 *
 * 引脚（可在 config.h 中覆盖）：
 *   SCL → GPIO 17 (SCK)
 *   SDA → GPIO 18 (MOSI)
 *   RS  → GPIO 21 (DC)
 *   CS  → GPIO 14
 *   RST → GPIO 48
 *
 * 显示内容随 assistantState 变化：
 *   IDLE      → 🙂 微笑脸
 *   RECORDING → 🎤 张嘴录音
 *   PROCESSING→ 🤔 思考（眼睛转圈）
 *   PLAYING   → 🗣 说话（嘴巴动画）
 *   SLEEP     → 😴 闭眼
 */

#ifndef OLED_DISPLAY_H
#define OLED_DISPLAY_H

#include <Arduino.h>
#include <SPI.h>
#include <U8g2lib.h>

// ──────────────────────────────────────────────────────────
//  CONFIG (override in config.h before including)
// ──────────────────────────────────────────────────────────
#ifndef OLED_ENABLED
  #define OLED_ENABLED   0
#endif
#ifndef LCD_SCK
  #define LCD_SCK  17
#endif
#ifndef LCD_MOSI
  #define LCD_MOSI 18
#endif
#ifndef LCD_DC
  #define LCD_DC   21
#endif
#ifndef LCD_CS
  #define LCD_CS   14
#endif
#ifndef LCD_RST
  #define LCD_RST  48
#endif

// ──────────────────────────────────────────────────────────
//  U8G2 对象
// ──────────────────────────────────────────────────────────
// LCD9648 (UC1701 / ST7565), 软件 SPI (SCK, MOSI, CS, DC, RST)
// ERC12864 init 与 UC1701 兼容
static U8G2_ST7565_ERC12864_F_4W_SW_SPI _lcd(U8G2_R0, LCD_SCK, LCD_MOSI, LCD_CS, LCD_DC, LCD_RST);

// ──────────────────────────────────────────────────────────
//  Face state machine
// ──────────────────────────────────────────────────────────
enum OledFace : uint8_t {
  FACE_HAPPY,       // IDLE
  FACE_LISTEN,      // RECORDING
  FACE_THINK,       // PROCESSING
  FACE_TALK,        // PLAYING
  FACE_SLEEP,       // SLEEP
  FACE_SAD,         // WiFi disconnected
};

static OledFace _currentFace = FACE_HAPPY;
static OledFace _targetFace  = FACE_HAPPY;
static unsigned long _lastAnimMs = 0;
static uint8_t _animFrame = 0;
static bool _lcdReady = false;

// ──────────────────────────────────────────────────────────
//  Drawing helpers
// ──────────────────────────────────────────────────────────

// 脸轮廓 (中心 64, 38；半径 22)
static void _drawFaceOutline() {
  _lcd.drawCircle(64, 38, 22);
}

// 眼睛 (x, y, 是否睁开)
static void _drawEye(int16_t x, int16_t y, bool open) {
  if (open) {
    _lcd.drawDisc(x, y, 4);               // 眼白
    _lcd.drawPixel(x, y);                 // 瞳孔（镂空效果取反）
  } else {
    _lcd.drawLine(x - 4, y, x + 4, y);    // 闭眼线
  }
}

// 嘴巴 (按表情)
static void _drawMouth(OledFace face) {
  switch (face) {
    case FACE_HAPPY:      // ^_^ 微笑
      _lcd.drawCircle(58, 46, 6, U8G2_DRAW_LOWER_LEFT | U8G2_DRAW_LOWER_RIGHT);
      _lcd.drawCircle(70, 46, 6, U8G2_DRAW_LOWER_LEFT | U8G2_DRAW_LOWER_RIGHT);
      _lcd.drawCircle(64, 48, 4, U8G2_DRAW_LOWER_LEFT | U8G2_DRAW_LOWER_RIGHT);
      break;
    case FACE_LISTEN:     // O_O 张嘴 (录音)
      _lcd.drawDisc(64, 48, 6);
      break;
    case FACE_THINK:      // o_O 思考 (横线)
      _lcd.drawLine(58, 48, 70, 48);
      break;
    case FACE_TALK:       // 说话 (嘴巴动画)
      switch (_animFrame % 3) {
        case 0:
          _lcd.drawCircle(58, 46, 6, U8G2_DRAW_LOWER_LEFT | U8G2_DRAW_LOWER_RIGHT);
          _lcd.drawCircle(70, 46, 6, U8G2_DRAW_LOWER_LEFT | U8G2_DRAW_LOWER_RIGHT);
          break;
        case 1:
          _lcd.drawDisc(64, 48, 5);
          break;
        case 2:
          _lcd.drawLine(58, 47, 70, 47);
          break;
      }
      break;
    case FACE_SLEEP:      // zzz
      _lcd.setCursor(54, 50);
      _lcd.print("zzz");
      break;
    case FACE_SAD:        // >_< 伤心
      _lcd.drawLine(56, 52, 72, 44);
      _lcd.drawLine(56, 44, 72, 52);
      break;
  }
}

// 状态栏 (顶部)
static void _drawStatusBar(bool wifiConnected, int rssiPercent) {
  if (wifiConnected) {
    _lcd.drawFrame(2, 1, 10, 6);   // 路由器图标
    uint8_t bars = map(constrain(rssiPercent, 0, 100), 0, 100, 1, 3);
    for (uint8_t i = 0; i < bars; i++) {
      _lcd.drawVLine(16 + i * 3, 5 - i * 2, i * 2 + 1);
    }
  } else {
    _lcd.drawLine(2, 1, 12, 7);
    _lcd.drawLine(2, 7, 12, 1);
  }
}

// ──────────────────────────────────────────────────────────
//  Public API
// ──────────────────────────────────────────────────────────

/// Init display. Call once in setup().
void oledInit() {
#if OLED_ENABLED
  _lcd.begin();
  _lcd.setContrast(128);   // 中档对比度

  // 测试：上半黑下半白
  _lcd.drawBox(0, 0, 128, 32);
  _lcd.sendBuffer();
  delay(1500);

  // 测试：左半黑右半白
  _lcd.clearBuffer();
  _lcd.drawBox(0, 0, 64, 64);
  _lcd.sendBuffer();
  delay(1500);

  // 测试：画 X 形粗对角线
  _lcd.clearBuffer();
  _lcd.drawBox(0, 0, 128, 4);
  _lcd.drawBox(0, 60, 128, 4);
  _lcd.drawBox(0, 0, 4, 64);
  _lcd.drawBox(124, 0, 4, 64);
  _lcd.sendBuffer();
  delay(1500);

  // 最终清屏
  _lcd.clearBuffer();
  _lcd.sendBuffer();

  _lcdReady = true;
  Serial.println("[LCD] ST7565 128x64 init OK");
#else
  _lcdReady = false;
#endif
}

/// Set target face.
void oledSetFace(OledFace face) {
  _targetFace = face;
}

/// Map AssistantState to OledFace.
OledFace oledFaceFromState(uint8_t state) {
  switch (state) {
    case 0:  return FACE_HAPPY;
    case 1:  return FACE_LISTEN;
    case 2:  return FACE_THINK;
    case 3:  return FACE_TALK;
    case 4:  return FACE_SLEEP;
    default: return FACE_HAPPY;
  }
}

/// Periodic tick — call from loop(). Returns true if buffer was sent.
bool oledTick(bool wifiConnected, int rssiPercent) {
  if (!_lcdReady) return false;

  unsigned long now = millis();

  if (_targetFace != _currentFace || now - _lastAnimMs >= 200) {
    _currentFace = _targetFace;
    _lastAnimMs = now;

    if (_currentFace == FACE_TALK) {
      _animFrame = (now / 120) % 3;
    } else if (_currentFace == FACE_THINK) {
      _animFrame = (now / 300) % 4;
    } else {
      _animFrame = 0;
    }

    _lcd.clearBuffer();

    _drawStatusBar(wifiConnected, rssiPercent);
    _drawFaceOutline();

    int16_t eyeLX = 52, eyeRX = 76;
    switch (_currentFace) {
      case FACE_HAPPY:
        _drawEye(eyeLX, 34, true);
        _drawEye(eyeRX, 34, true);
        break;
      case FACE_LISTEN:
        _drawEye(eyeLX, 34, true);
        _drawEye(eyeRX, 34, true);
        break;
      case FACE_THINK:
        _drawEye(eyeLX - (_animFrame % 2), 34, _animFrame % 3 != 0);
        _drawEye(eyeRX, 34, true);
        break;
      case FACE_TALK:
        _drawEye(eyeLX, 34, true);
        _drawEye(eyeRX, 34, true);
        break;
      case FACE_SLEEP:
        _drawEye(eyeLX, 34, false);
        _drawEye(eyeRX, 34, false);
        break;
      case FACE_SAD:
        _lcd.drawLine(eyeLX - 3, 32, eyeLX + 3, 36);
        _lcd.drawLine(eyeRX - 3, 32, eyeRX + 3, 36);
        break;
    }

    _drawMouth(_currentFace);

    _lcd.setCursor(32, 57);
    switch (_currentFace) {
      case FACE_HAPPY:   _lcd.print("Hi~"); break;
      case FACE_LISTEN:  _lcd.print("Listening.."); break;
      case FACE_THINK:   _lcd.print("Thinking"); break;
      case FACE_TALK:    _lcd.print("Speaking"); break;
      case FACE_SLEEP:   _lcd.print("Good night"); break;
      case FACE_SAD:     _lcd.print("No WiFi"); break;
    }

    _lcd.sendBuffer();
    return true;
  }
  return false;
}

#endif // OLED_DISPLAY_H
