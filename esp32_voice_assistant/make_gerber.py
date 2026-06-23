#!/usr/bin/env python3
"""
ESP32-S3 玩偶底板 - 接驳板 Gerber
只放排母插座 + 电源走线，信号线用杜邦线接
尺寸 65x65mm
"""
import os, zipfile

OUT = "D:/玩偶/esp32_voice_assistant/gerber_output"
os.makedirs(OUT, exist_ok=True)

W, H = 65.0, 65.0  # 板尺寸

P = 2.54  # 间距

# ESP32 插座中心 (让出上下空间给其他模块)
ESP_X, ESP_Y = 32.5, 32.0
ESP_START_Y = ESP_Y - 9 * P   # 19 pin, 中心是第10pin

# 左排 X
ESP_LX = ESP_X - 5 * P
ESP_RX = ESP_X + 5 * P

class GF:
    """Gerber 文件"""
    def __init__(self, path, unit="MM"):
        self.path = path
        self.lines = []
        self.lines.append(f"G04 ESP32 Voice Carrier*")
        self.lines.append("%FSLAX26Y26*%")
        self.lines.append("%MOMM*%")
        self.ap = {}
        self.next_ap = 10

    def ap_circle(self, dia, name=None):
        n = self.next_ap; self.next_ap += 1
        self.lines.append(f"%ADD{n}C,{dia}*%")
        self.ap[name or n] = n
        return n

    def ap_rect(self, sx, sy, name=None):
        n = self.next_ap; self.next_ap += 1
        self.lines.append(f"%ADD{n}R,{sx}X{sy}*%")
        self.ap[name or n] = n
        return n

    def flash(self, ap_name, x, y):
        n = self.ap.get(ap_name, ap_name)
        xs, ys = f"{x*100000:.0f}", f"{y*100000:.0f}"
        self.lines.append(f"D{n}*")
        self.lines.append(f"X{xs}Y{ys}D03*")

    def move(self, x, y):
        xs, ys = f"{x*100000:.0f}", f"{y*100000:.0f}"
        self.lines.append(f"X{xs}Y{ys}D02*")

    def draw(self, x, y):
        xs, ys = f"{x*100000:.0f}", f"{y*100000:.0f}"
        self.lines.append(f"X{xs}Y{ys}D01*")

    def select(self, ap_name):
        n = self.ap.get(ap_name, ap_name)
        self.lines.append(f"D{n}*")

    def line(self, x1, y1, x2, y2, width):
        self.ap_circle(str(width), f"L{width}")
        self.select(f"L{width}")
        self.move(x1, y1); self.draw(x2, y2)

    def rect(self, x1, y1, x2, y2, width=0.1):
        self.ap_circle(str(width), f"R{width}")
        self.select(f"R{width}")
        self.move(x1, y1); self.draw(x2, y1)
        self.draw(x2, y2); self.draw(x1, y2); self.draw(x1, y1)

    def close(self):
        self.lines.append("M02*")
        with open(self.path, "w") as f:
            f.write("\n".join(self.lines))

class DRL:
    """NC Drill"""
    def __init__(self, path):
        self.path = path
        self.l = []
        self.l.append("M48")
        self.l.append("FMAT,2")
        self.l.append("METRIC,TZ")
        self.l.append("T01C1.0")  # 1.0mm drill
        self.l.append("T02C0.8")
        self.l.append("T03C1.3")
        self.l.append("%")
        self.t = None
    def hole(self, x, y, t="T01"):
        if t != self.t:
            if self.t: self.l.append("T0")
            self.l.append(t); self.t = t
        self.l.append(f"X{x*100000:.0f}Y{y*100000:.0f}")
    def close(self):
        self.l.append("T0"); self.l.append("M30")
        with open(self.path, "w") as f: f.write("\n".join(self.l))

def pad(top, drl, x, y, dia=1.8, hole=1.0, tool="T01"):
    """通孔焊盘"""
    top.ap_circle(str(dia), f"D{dia}")
    top.flash(f"D{dia}", x, y)
    drl.hole(x, y, tool)

def make():
    top = GF(f"{OUT}/esp32_voice.GTL")
    # 阻焊层
    top_ml = GF(f"{OUT}/esp32_voice.GTS")
    # 丝印
    top_si = GF(f"{OUT}/esp32_voice.GTO")
    # 底层
    bot = GF(f"{OUT}/esp32_voice.GBL")
    # 板框
    edg = GF(f"{OUT}/esp32_voice.GML")
    # 钻孔
    drl = DRL(f"{OUT}/esp32_voice.TXT")

    # ===== 板框 =====
    edg.rect(0, 0, W, H, 0.1)

    # ===== ESP32 双排母座 (2x19) =====
    for row in range(19):
        ry = ESP_START_Y + row * P
        # 左排（J1）
        pad(top, drl, ESP_LX, ry)
        # 阻焊开窗
        top_ml.ap_circle("2.2", f"MSK{row}L"); top_ml.flash(f"MSK{row}L", ESP_LX, ry)
        # 右排（J2）
        pad(top, drl, ESP_RX, ry)
        top_ml.ap_circle("2.2", f"MSK{row}R"); top_ml.flash(f"MSK{row}R", ESP_RX, ry)

    # ===== 模块排针 =====
    # INMP441 (1x6, 左边缘)
    mic_x, mic_y = 3, 20
    for i in range(6):
        pad(top, drl, mic_x, mic_y + i*P)
        top_ml.ap_circle("2.2", f"MM{i}"); top_ml.flash(f"MM{i}", mic_x, mic_y + i*P)

    # 按钮 (1x2, INMP441 下方)
    btn_x, btn_y = 3, 39
    for i in range(2):
        pad(top, drl, btn_x, btn_y + i*P)
        top_ml.ap_circle("2.2", f"MB{i}"); top_ml.flash(f"MB{i}", btn_x, btn_y + i*P)

    # MAX98357 (1x7, 右边缘)
    spk_x, spk_y = 62, 20
    for i in range(7):
        pad(top, drl, spk_x, spk_y + i*P)
        top_ml.ap_circle("2.2", f"MS{i}"); top_ml.flash(f"MS{i}", spk_x, spk_y + i*P)

    # SHT3X (1x4, 顶边缘左)
    sht_x, sht_y = 15, 3
    for i in range(4):
        pad(top, drl, sht_x + i*P, sht_y)
        top_ml.ap_circle("2.2", f"MH{i}"); top_ml.flash(f"MH{i}", sht_x + i*P, sht_y)

    # OLED (1x4, 顶边缘右)
    oled_x, oled_y = 35, 3
    for i in range(4):
        pad(top, drl, oled_x + i*P, oled_y)
        top_ml.ap_circle("2.2", f"MO{i}"); top_ml.flash(f"MO{i}", oled_x + i*P, oled_y)

    # 喇叭端子 (2P, 右下)
    sp_tx, sp_ty = 62, 52
    pad(top, drl, sp_tx, sp_ty, 2.5, 1.3, "T03")
    pad(top, drl, sp_tx+5.08, sp_ty, 2.5, 1.3, "T03")
    top_ml.ap_circle("3.0", "MSKSP"); top_ml.flash("MSKSP", sp_tx, sp_ty)
    top_ml.flash("MSKSP", sp_tx+5.08, sp_ty)

    # 电池 JST (2P, 底边)
    bat_x, bat_y = 15, 62
    pad(top, drl, bat_x, bat_y, 2.0, 0.8, "T02")
    pad(top, drl, bat_x+2, bat_y, 2.0, 0.8, "T02")
    top_ml.ap_circle("2.5", "MSKB1"); top_ml.flash("MSKB1", bat_x, bat_y)
    top_ml.flash("MSKB1", bat_x+2, bat_y)

    # ZX-056 (1x4, 底边右)
    zx_x, zx_y = 40, 62
    for i in range(4):
        pad(top, drl, zx_x + i*P, zx_y)
        top_ml.ap_circle("2.2", f"MZ{i}"); top_ml.flash(f"MZ{i}", zx_x + i*P, zx_y)

    # ===== 电源走线 (GND 与 3.3V/5V) =====
    # 3.3V 粗线：从 ESP 左排第1脚到 3.3V 焊盘
    v33_y = ESP_START_Y  # 第1行
    top.line(ESP_LX-2, v33_y, 0, v33_y, 1.0)  # 3.3V 走左侧

    # GND 走线
    gnd_y = ESP_START_Y  # 右排第1行
    top.line(ESP_RX+2, gnd_y, W, gnd_y, 1.0)  # GND 走右侧

    # 5V 走线
    _5v_y = ESP_START_Y + P  # 右排第2行
    top.line(ESP_RX+2, _5v_y, W, _5v_y, 1.0)

    # ===== 丝印 (标签) =====
    top_si.ap_circle("0.15", "SI")
    top_si.select("SI")
    # 画排针轮廓
    # INMP441 框
    top_si.rect(mic_x-1, mic_y-1, mic_x+1, mic_y+5*P+1, 0.15)
    # 按钮框
    top_si.rect(btn_x-1, btn_y-1, btn_x+1, btn_y+P+1, 0.15)
    # MAX98357 框
    top_si.rect(spk_x-1, spk_y-1, spk_x+1, spk_y+6*P+1, 0.15)
    # SHT3X 框
    top_si.rect(sht_x-1, sht_y-1, sht_x+3*P+1, sht_y+1, 0.15)
    # OLED 框
    top_si.rect(oled_x-1, oled_y-1, oled_x+3*P+1, oled_y+1, 0.15)

    # 标签文字 (用走线画简单标识)
    # 底部品牌名等
    top_si.select("SI")
    top_si.move(15, 50); top_si.draw(20, 50)  # INMP441 标签
    top_si.move(45, 50); top_si.draw(50, 50)  # MAX98357 标签

    # ===== 关闭所有文件 =====
    for g in [top, bot, top_ml, top_si, edg]:
        g.close()
    # 底层阻焊 (空)
    bot_ml = GF(f"{OUT}/esp32_voice.GBS")
    bot_ml.close()
    # 底层丝印 (空)
    bot_si = GF(f"{OUT}/esp32_voice.GBO")
    bot_si.close()
    drl.close()

    # ===== 打包 =====
    zip_path = f"{OUT}/esp32_voice_gerber.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for fn in os.listdir(OUT):
            if fn.endswith((".GTL",".GBL",".GTS",".GBS",".GTO",".GBO",".GML",".TXT")):
                zf.write(os.path.join(OUT, fn), arcname=fn)
    print(f"Gerber: {zip_path}")
    print(f"Board: {W}x{H}mm")

if __name__ == "__main__":
    make()
