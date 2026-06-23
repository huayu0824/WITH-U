#!/usr/bin/env python3
"""
生成 ESP32-S3 玩偶底板 PCB
输出: KiCad PCB 格式 (可导入 KiCad / 在线转 Gerber)
"""

import math

MM = 1.0  # KiCad uses mm by default

# ========= 常量 =========
W = 80     # PCB 宽 mm
H = 90     # PCB 高 mm

# ESP32 DevKitC (双排 2x19 针, 2.54mm)
ESP_COLS = 19
ESP_PITCH = 2.54
ESP_ROW_SPACING = 25.4  # 两排之间距离
ESP_X = 40     # ESP 中心 X
ESP_Y = 35     # ESP 中心 Y

# ========= 辅助函数 =========
def fmt_xy(x, y, ref=None):
    return f'    (at {x} {y})'

def gen_header():
    return """(kicad_pcb (version 20221018) (generator "esp32_voice_pcb_gen")

  (general
    (thickness 1.6)
  )

  (layers
    (0 "F.Cu" signal)
    (31 "B.Cu" signal)
    (F.CrtYd "F.CrtYd" user)
    (B.CrtYd "B.CrtYd" user)
    (F.Fab "F.Fab" user)
    (B.Fab "B.Fab" user)
    (F.SilkS "F.SilkS" user)
    (B.SilkS "B.SilkS" user)
    (F.Mask "F.Mask" user)
    (B.Mask "B.Mask" user)
    (Edge.Cuts "Edge.Cuts" user)
    (Margin "Margin" user)
    (F.Paste "F.Paste" user)
    (B.Paste "B.Paste" user)
  )

  (setup
    (pad_to_mask_clearance 0.05)
    (pad_to_paste_clearance 0)
    (allow_soldermask_bridges_in_footprints false)
  )
"""

def gen_nets():
    nets = [
        (0, ""),
        (1, "GND"),
        (2, "3V3"),
        (3, "5V"),
        (4, "MIC_BCLK"),
        (5, "MIC_WS"),
        (6, "MIC_DIN"),
        (7, "SPK_BCLK"),
        (8, "SPK_WS"),
        (9, "SPK_DOUT"),
        (10, "I2C_SDA"),
        (11, "I2C_SCL"),
        (12, "BTN"),
        (13, "SPK_OUT"),
        (14, "BAT_IN"),
    ]
    return "\n".join(f'  (net {n} "{name}")' for n, name in nets)

def gen_footer():
    return ")"

def gen_edge_cuts():
    """板框"""
    return f'''
  (footprint "edge:edge"
    (layer "Edge.Cuts")
    (attr board_only)
    (fp_line (start 0 0) (end {W} 0) (layer "Edge.Cuts") (width 0.05))
    (fp_line (start {W} 0) (end {W} {H}) (layer "Edge.Cuts") (width 0.05))
    (fp_line (start {W} {H}) (end 0 {H}) (layer "Edge.Cuts") (width 0.05))
    (fp_line (start 0 {H}) (end 0 0) (layer "Edge.Cuts") (width 0.05))
  )'''

def gen_via(x, y, net=0):
    """过孔"""
    return f'''
  (via (at {x} {y}) (size 1.0) (drill 0.5) (layers "F.Cu" "B.Cu") (net {net}))'''

def gen_track(x1, y1, x2, y2, layer="F.Cu", width=0.5, net=0):
    """走线"""
    return f'''
  (segment (start {x1} {y1}) (end {x2} {y2}) (width {width}) (layer "{layer}") (net {net}))'''

def gen_pin_header(name, x, y, cols=1, pitch=2.54, orientation="vertical", net_start=0):
    """生成排针"""
    out = f'''
  (footprint "Connector_PinHeader_2.54mm:PinHeader_{cols}x1_P2.54mm_Vertical"
    (layer "F.Cu")
    (tedit 0)
    (at {x} {y})
    (descr "{name}")'''

    if orientation == "horizontal":
        out += "\n    (rotate 90)"

    out += '''
    (tags "pin header")
    (attr through_hole)
    (fp_text reference "J_{}")'''.format(name)

    out += f'''
    (fp_text value "{name}" (at 0 -3) (layer "F.Fab") (hide yes))'''

    # Each pin
    for i in range(cols):
        px = i * pitch
        net = net_start + i if net_start > 0 else 0
        out += f'''
    (pad {i+1} thru_hole circle (at {px} 0) (size 1.8 1.8) (drill 1.0) (layers *.Cu *.Mask) (net {net}) (solder_mask_margin 0.1))'''

    out += "\n  )"
    return out

def gen_esp32_s3_connector():
    """ESP32-S3-DevKitC-1 的双排母座"""
    out = ""
    # 左侧排针 (J1) - 19 pin - X=ESP_X - row/2, Y=top
    j1_x = ESP_X - ESP_ROW_SPACING / 2
    j2_x = ESP_X + ESP_ROW_SPACING / 2
    start_y = ESP_Y - (ESP_COLS * ESP_PITCH) / 2

    out += f'''
  (footprint "Connector_PinSocket_2.54mm:PinSocket_2x19_P2.54mm_Vertical"
    (layer "F.Cu")
    (tedit 0)
    (at {ESP_X} {ESP_Y})
    (descr "ESP32-S3 DevKitC-1 Socket")
    (tags "socket")
    (attr through_hole)
    (fp_text reference "J_ESP32")
    (fp_text value "ESP32-S3" (at 0 -{ESP_COLS*ESP_PITCH/2+3}) (layer "F.Fab") (hide yes))'''

    # 引脚映射：接线按照 config.h
    # J1 (左): GPIOs    J2 (右): 电源等
    left_pins = {  # J1, 从板子底部开始编号 (靠近USB端朝上)
        1: (2, "3V3"),    # 3V3
        2: (10, "I2C_SDA"),  # GPIO41 = SDA
        3: (11, "I2C_SCL"),  # GPIO42 = SCL
        4: (1, "GND"),
        5: (0, ""),      # GPIO0 = 按钮
        6: (0, ""),      # GPIO1
        7: (0, ""),      # GPIO2
        8: (0, ""),      # GPIO3
        9: (4, "MIC_BCLK"),   # GPIO4/5 实际看板子
        10: (5, "MIC_WS"),
        11: (6, "MIC_DIN"),
        12: (7, "SPK_DOUT"),
        13: (0, ""),
        14: (0, ""),
        15: (8, "SPK_WS"),    # GPIO16
        16: (9, "SPK_BCLK"),  # GPIO15
        17: (3, "5V"),    # VUSB
        18: (1, "GND"),
        19: (0, "EN"),
    }

    right_pins = {
        1: (1, "GND"),
        2: (3, "5V"),
        3: (0, ""),
        4: (0, ""),
        5: (0, ""),
        6: (0, ""),
        7: (0, ""),
        8: (0, ""),
        9: (0, ""),
        10: (0, ""),
        11: (0, ""),
        12: (0, ""),
        13: (0, ""),
        14: (0, ""),
        15: (0, ""),
        16: (0, ""),
        17: (0, ""),
        18: (0, ""),
        19: (2, "3V3"),
    }

    # 生成引脚
    for i, (net, name) in left_pins.items():
        ry = start_y + (i-1) * ESP_PITCH
        out += f'''
    (pad "{i}A" thru_hole circle (at {-ESP_ROW_SPACING/2} {ry}) (size 1.8 1.8) (drill 1.0) (layers *.Cu *.Mask) (net {net}))'''

    for i, (net, name) in right_pins.items():
        ry = start_y + (i-1) * ESP_PITCH
        out += f'''
    (pad "{i}B" thru_hole circle (at {ESP_ROW_SPACING/2} {ry}) (size 1.8 1.8) (drill 1.0) (layers *.Cu *.Mask) (net {net}))'''

    out += "\n  )"
    return out

def gen_module_connectors():
    """各模块的排针/接线端子"""
    out = ""

    # INMP441 模块 (6 pin: VCC, GND, DOUT, BCLK, WS, L/R)
    mx = 8
    my = 68
    out += f'''
  (footprint "Connector_PinHeader_2.54mm:PinHeader_1x6_P2.54mm_Vertical"
    (layer "F.Cu") (at {mx} {my})
    (descr "INMP441") (tags "header")
    (attr through_hole)
    (fp_text reference "J_MIC") (fp_text value "INMP441" (at 0 -4) (hide yes))
    (pad 1 thru_hole circle (at 0 0) (size 1.8 1.8) (drill 1.0) (layers *.Cu *.Mask) (net 2))    (pad 2 thru_hole circle (at 2.54 0) (size 1.8 1.8) (drill 1.0) (layers *.Cu *.Mask) (net 1))
    (pad 3 thru_hole circle (at 5.08 0) (size 1.8 1.8) (drill 1.0) (layers *.Cu *.Mask) (net 6))
    (pad 4 thru_hole circle (at 7.62 0) (size 1.8 1.8) (drill 1.0) (layers *.Cu *.Mask) (net 4))
    (pad 5 thru_hole circle (at 10.16 0) (size 1.8 1.8) (drill 1.0) (layers *.Cu *.Mask) (net 5))
    (pad 6 thru_hole circle (at 12.7 0) (size 1.8 1.8) (drill 1.0) (layers *.Cu *.Mask) (net 1))
  )
  (fp_text user "INMP441" (at {mx} {my-5}) (layer "F.SilkS"))'''

    # MAX98357 模块 (7 pin: VIN, GND, BCLK, LRC, DIN, GAIN, SD)
    sx = 68
    sy = 68
    out += f'''
  (footprint "Connector_PinHeader_2.54mm:PinHeader_1x7_P2.54mm_Vertical"
    (layer "F.Cu") (at {sx} {sy})
    (descr "MAX98357") (tags "header")
    (attr through_hole)
    (fp_text reference "J_SPK") (fp_text value "MAX98357" (at 0 -4) (hide yes))
    (pad 1 thru_hole circle (at 0 0) (size 1.8 1.8) (drill 1.0) (layers *.Cu *.Mask) (net 3))    (pad 2 thru_hole circle (at 2.54 0) (size 1.8 1.8) (drill 1.0) (layers *.Cu *.Mask) (net 1))
    (pad 3 thru_hole circle (at 5.08 0) (size 1.8 1.8) (drill 1.0) (layers *.Cu *.Mask) (net 9))
    (pad 4 thru_hole circle (at 7.62 0) (size 1.8 1.8) (drill 1.0) (layers *.Cu *.Mask) (net 8))
    (pad 5 thru_hole circle (at 10.16 0) (size 1.8 1.8) (drill 1.0) (layers *.Cu *.Mask) (net 7))
    (pad 6 thru_hole circle (at 12.7 0) (size 1.8 1.8) (drill 1.0) (layers *.Cu *.Mask) (net 1))
    (pad 7 thru_hole circle (at 15.24 0) (size 1.8 1.8) (drill 1.0) (layers *.Cu *.Mask) (net 3))
  )
  (fp_text user "MAX98357" (at {sx} {sy-5}) (layer "F.SilkS"))'''

    # SHT3X + OLED (I2C 共用, 4 pin: VCC, GND, SDA, SCL)
    i2c_x = 25
    i2c_y = 10
    out += f'''
  (footprint "Connector_PinHeader_2.54mm:PinHeader_1x4_P2.54mm_Vertical"
    (layer "F.Cu") (at {i2c_x} {i2c_y})
    (descr "I2C Bus") (tags "header")
    (attr through_hole)
    (fp_text reference "J_SHT3X") (fp_text value "SHT3X" (at 0 -4) (hide yes))
    (pad 1 thru_hole circle (at 0 0) (size 1.8 1.8) (drill 1.0) (layers *.Cu *.Mask) (net 2))
    (pad 2 thru_hole circle (at 2.54 0) (size 1.8 1.8) (drill 1.0) (layers *.Cu *.Mask) (net 1))
    (pad 3 thru_hole circle (at 5.08 0) (size 1.8 1.8) (drill 1.0) (layers *.Cu *.Mask) (net 10))
    (pad 4 thru_hole circle (at 7.62 0) (size 1.8 1.8) (drill 1.0) (layers *.Cu *.Mask) (net 11))
  )
  (fp_text user "I2C(SHT3X+OLED)" (at {i2c_x} {i2c_y-5}) (layer "F.SilkS"))'''

    # OLED 并行接口 (同 I2C 总线)
    oled_x = 52
    oled_y = 10
    out += f'''
  (footprint "Connector_PinHeader_2.54mm:PinHeader_1x4_P2.54mm_Vertical"
    (layer "F.Cu") (at {oled_x} {oled_y})
    (descr "OLED SSD1306") (tags "header")
    (attr through_hole)
    (fp_text reference "J_OLED") (fp_text value "OLED" (at 0 -4) (hide yes))
    (pad 1 thru_hole circle (at 0 0) (size 1.8 1.8) (drill 1.0) (layers *.Cu *.Mask) (net 2))
    (pad 2 thru_hole circle (at 2.54 0) (size 1.8 1.8) (drill 1.0) (layers *.Cu *.Mask) (net 1))
    (pad 3 thru_hole circle (at 5.08 0) (size 1.8 1.8) (drill 1.0) (layers *.Cu *.Mask) (net 10))
    (pad 4 thru_hole circle (at 7.62 0) (size 1.8 1.8) (drill 1.0) (layers *.Cu *.Mask) (net 11))
  )
  (fp_text user "OLED" (at {oled_x} {oled_y-5}) (layer "F.SilkS"))'''

    # 按钮 (2 pin: GPIO0, GND)
    btn_x = 8
    btn_y = 15
    out += f'''
  (footprint "Button_Switch_SMD:SW_SPST_TL3342"
    (layer "F.Cu") (at {btn_x} {btn_y})
    (descr "Button") (tags "button")
    (attr through_hole)
    (fp_text reference "SW1") (fp_text value "BTN" (at 0 -4) (hide yes))
    (pad 1 thru_hole circle (at 0 0) (size 1.8 1.8) (drill 1.0) (layers *.Cu *.Mask) (net 12))
    (pad 2 thru_hole circle (at 3 0) (size 1.8 1.8) (drill 1.0) (layers *.Cu *.Mask) (net 1))
  )'''

    # 喇叭端子 (2 pin screw terminal)
    spk_x = 72
    spk_y = 15
    out += f'''
  (footprint "TerminalBlock:TerminalBlock_bornier-2_P5.08mm"
    (layer "F.Cu") (at {spk_x} {spk_y})
    (descr "Speaker") (tags "screw")
    (attr through_hole)
    (fp_text reference "SPK1") (fp_text value "SPEAKER" (at 0 -4) (hide yes))
    (pad 1 thru_hole circle (at 0 0) (size 2.5 2.5) (drill 1.3) (layers *.Cu *.Mask) (net 13))
    (pad 2 thru_hole circle (at 5.08 0) (size 2.5 2.5) (drill 1.3) (layers *.Cu *.Mask) (net 1))
  )
  (fp_text user "喇叭" (at {spk_x} {spk_y-5}) (layer "F.SilkS"))'''

    # 电池接口 (2 pin JST)
    bat_x = 72
    bat_y = 82
    out += f'''
  (footprint "Connector_JST:JST_PH_B2B-PH-K_1x02_P2.0mm_Vertical"
    (layer "F.Cu") (at {bat_x} {bat_y})
    (descr "Battery") (tags "jst")
    (attr through_hole)
    (fp_text reference "BAT1") (fp_text value "BATTERY" (at 0 -4) (hide yes))
    (pad 1 thru_hole circle (at 0 0) (size 2.0 2.0) (drill 0.8) (layers *.Cu *.Mask) (net 3))
    (pad 2 thru_hole circle (at 2 0) (size 2.0 2.0) (drill 0.8) (layers *.Cu *.Mask) (net 1))
  )
  (fp_text user "电池 3.7V" (at {bat_x} {bat_y+4}) (layer "F.SilkS"))'''

    # ZX-056 充电模块 (4 pin: B+, B-, OUT+, OUT-)
    zx_x = 8
    zx_y = 82
    out += f'''
  (footprint "Connector_PinHeader_2.54mm:PinHeader_1x4_P2.54mm_Vertical"
    (layer "F.Cu") (at {zx_x} {zx_y})
    (descr "ZX-056") (tags "header")
    (attr through_hole)
    (fp_text reference "J_CHG") (fp_text value "CHARGER" (at 0 -4) (hide yes))
    (pad 1 thru_hole circle (at 0 0) (size 1.8 1.8) (drill 1.0) (layers *.Cu *.Mask) (net 14))
    (pad 2 thru_hole circle (at 2.54 0) (size 1.8 1.8) (drill 1.0) (layers *.Cu *.Mask) (net 1))
    (pad 3 thru_hole circle (at 5.08 0) (size 1.8 1.8) (drill 1.0) (layers *.Cu *.Mask) (net 3))
    (pad 4 thru_hole circle (at 7.62 0) (size 1.8 1.8) (drill 1.0) (layers *.Cu *.Mask) (net 1))
  )
  (fp_text user "ZX-056充电" (at {zx_x} {zx_y-5}) (layer "F.SilkS"))'''

    return out

def gen_routing():
    """生成走线（简化的自动布线）"""
    traces = []

    # GND 铜皮区域
    traces.append(f'''
  (filled_polygon (layer "F.Cu") (net 1)
    (pts
      (xy 5 5) (xy 75 5) (xy 75 85) (xy 5 85)
    )
  )''')

    return "\n".join(traces)

def main():
    pcb = gen_header()
    pcb += "\n" + gen_nets()
    pcb += "\n" + gen_edge_cuts()
    pcb += "\n" + gen_esp32_s3_connector()
    pcb += "\n" + gen_module_connectors()
    pcb += "\n" + gen_routing()
    pcb += "\n" + gen_footer()

    output_path = "D:/玩偶/esp32_voice_assistant/server/esp32_voice_carrier.kicad_pcb"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(pcb)

    print(f"PCB 文件已生成: {output_path}")
    print(f"文件大小: {len(pcb)} 字节")

    # 同时生成部件位置说明
    doc = f"""
============================================
ESP32-S3 玩偶底板 - 部件位置及接线说明
============================================
板尺寸: {W}mm × {H}mm
层数: 2层 (双面板)

=== 各模块接线 ===

1. INMP441 (J_MIC) - 左上区域
   引脚1: VCC → 3.3V
   引脚2: GND
   引脚3: DOUT → GPIO6
   引脚4: BCLK → GPIO5
   引脚5: WS   → GPIO4
   引脚6: L/R  → GND

2. MAX98357 (J_SPK) - 右上区域
   引脚1: VIN  → 5V
   引脚2: GND
   引脚3: BCLK → GPIO15
   引脚4: LRC  → GPIO16
   引脚5: DIN  → GPIO7
   引脚6: GAIN → GND
   引脚7: SD   → 3.3V (或悬空)

3. SHT3X (J_SHT3X) - I2C总线
   引脚1: VCC  → 3.3V
   引脚2: GND
   引脚3: SDA  → GPIO41
   引脚4: SCL  → GPIO42

4. OLED (J_OLED) - 同I2C总线 (SHT3X并联)
   引脚1: VCC  → 3.3V
   引脚2: GND
   引脚3: SDA  → GPIO41
   引脚4: SCL  → GPIO42

5. 按钮 (SW1) - 左下方
   引脚1: GPIO0
   引脚2: GND

6. 喇叭 (SPK1) - 右下方
   OUT+ → MAX98357 SPK+
   OUT- → MAX98357 SPK-

7. ZX-056充电模块 (J_CHG) - 底部左侧
   引脚1: B+  → 电池正极
   引脚2: B-  → 电池负极
   引脚3: OUT+ → 5V输出
   引脚4: OUT- → GND

8. 电池接口 (BAT1) - 底部右侧
   引脚1: 电池正极
   引脚2: 电池负极

=== 使用说明 ===

1. ESP32-S3 开发板插在板中央的双排母座上
2. 各模块插在对应排针上
3. 喇叭拧在接线端子上
4. 电池插在 JST 接口上
5. 用 Type-C 线插 ESP32 供电/烧录

=== 注意 ===
- 此 PCB 为 KiCad 格式，需用 KiCad 打开查看/导出 Gerber
- 或上传 https://kicad.github.io/ 在线转换
- 打样建议 1.6mm 板厚，FR-4 材质，喷锡工艺
============================================
"""
    doc_path = "D:/玩偶/esp32_voice_assistant/server/pcb_guide.txt"
    with open(doc_path, "w", encoding="utf-8") as f:
        f.write(doc)
    print(f"使用说明已生成: {doc_path}")

if __name__ == "__main__":
    main()
