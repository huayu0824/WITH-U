#!/usr/bin/env python3
"""解析 Gerber 文件，输出文字版布局图"""
import re, math

def parse_gerber_gtl(path):
    """解析顶层 Gerber 文件，提取焊盘位置"""
    pads = []
    with open(path) as f:
        content = f.read()

    # 查找所有 D03 (flash) 命令
    for match in re.finditer(r'X(\d+)Y(\d+)D03\*', content):
        x = int(match.group(1)) / 100000
        y = int(match.group(2)) / 100000
        pads.append((x, y))
    return pads

def parse_drill(path):
    """解析钻孔文件"""
    holes = []
    with open(path) as f:
        content = f.read()

    for match in re.finditer(r'X(\d+)Y(\d+)', content):
        x = int(match.group(1)) / 100000
        y = int(match.group(2)) / 100000
        holes.append((x, y))
    return holes

def visualize(name, pads, board_w=65, board_h=65):
    """生成文字布局图"""
    # 统计每个区域的焊盘数量
    grid = [[' ' for _ in range(int(board_w/5)+1)] for _ in range(int(board_h/5)+1)]

    for x, y in pads:
        gx = min(int(x/5), len(grid[0])-1)
        gy = min(int(y/5), len(grid)-1)
        grid[gy][gx] = '●'

    print(f"\n===== {name} ===== ({board_w}x{board_h}mm)")
    print(f"Total pads: {len(pads)}")
    print()

    # 打印网格 (Y反转)
    for row_idx in range(len(grid)-1, -1, -1):
        y_label = f"{row_idx*5:2d}-{(row_idx+1)*5:2d}mm"
        print(f"{y_label} | " + "".join(grid[row_idx]))
    print("      " + "-" * (len(grid[0])*2))
    x_labels = "      " + "  ".join(f"{i*5}" for i in range(len(grid[0])))
    print(x_labels)
    print()

def group_by_region(pads, board_w=65, board_h=65):
    """按区域分组焊盘"""
    # 左边缘 (x < 10)
    left = sorted([(x,y) for x,y in pads if x < 10], key=lambda p: p[1])
    # 右边缘 (x > board_w - 10)
    right = sorted([(x,y) for x,y in pads if x > board_w - 10], key=lambda p: p[1])
    # 顶边缘 (y < 8)
    top = sorted([(x,y) for x,y in pads if y < 8], key=lambda p: p[0])
    # 底边缘 (y > board_h - 8)
    bottom = sorted([(x,y) for x,y in pads if y > board_h - 8], key=lambda p: p[0])
    # 中间 (ESP32 插座)
    center = sorted([(x,y) for x,y in pads if 10 <= x <= board_w-10 and 8 <= y <= board_h-8], key=lambda p: (p[0], p[1]))

    print("\n===== 焊盘分组 =====\n")

    print(f"【左侧】INMP441(6) + 按钮(2) — {len(left)}个焊盘")
    for x,y in left:
        print(f"  X={x:.1f} Y={y:.1f}")

    print(f"\n【右侧】MAX98357(7) + 喇叭端子(2) — {len(right)}个焊盘")
    for x,y in right:
        print(f"  X={x:.1f} Y={y:.1f}")

    print(f"\n【顶部】SHT3X(4) + OLED(4) — {len(top)}个焊盘")
    for x,y in top:
        print(f"  X={x:.1f} Y={y:.1f}")

    print(f"\n【底部】电池(2) + ZX-056(4) — {len(bottom)}个焊盘")
    for x,y in bottom:
        print(f"  X={x:.1f} Y={y:.1f}")

    print(f"\n【中央】ESP32 双排母座 — {len(center)}个焊盘")
    # 分组为左右两排
    if center:
        left_row = sorted([(x,y) for x,y in center if x < 25], key=lambda p: p[1])
        right_row = sorted([(x,y) for x,y in center if x >= 25], key=lambda p: p[1])
        print(f"  左排 (J1): {len(left_row)}个")
        if left_row:
            print(f"    首: X={left_row[0][0]:.1f} Y={left_row[0][1]:.1f}")
            print(f"    末: X={left_row[-1][0]:.1f} Y={left_row[-1][1]:.1f}")
            spacing = round(left_row[1][1] - left_row[0][1], 2) if len(left_row) > 1 else 0
            print(f"    间距: {spacing}mm (应2.54)")
        print(f"  右排 (J2): {len(right_row)}个")
        if right_row:
            print(f"    首: X={right_row[0][0]:.1f} Y={right_row[0][1]:.1f}")
            spacing = round(right_row[1][1] - right_row[0][1], 2) if len(right_row) > 1 else 0
            print(f"    间距: {spacing}mm (应2.54)")

    # 检查最小焊盘间距
    print(f"\n===== 间距检查 =====\n")
    all_pads = sorted(pads)
    min_gap = 100
    for i in range(len(all_pads)):
        for j in range(i+1, min(i+10, len(all_pads))):
            dx = all_pads[i][0] - all_pads[j][0]
            dy = all_pads[i][1] - all_pads[j][1]
            dist = math.sqrt(dx*dx + dy*dy)
            if 0 < dist < min_gap:
                min_gap = dist
    print(f"最小焊盘间距: {min_gap:.1f}mm (需>1.5mm)")
    if min_gap < 1.5:
        print(f"⚠️  间距过近！可能有短路风险")
    else:
        print(f"✅ 间距安全")


import sys
gerber_dir = "D:/玩偶/esp32_voice_assistant/gerber_output"
gtl = f"{gerber_dir}/esp32_voice.GTL"
drl = f"{gerber_dir}/esp32_voice.TXT"

pads = parse_gerber_gtl(gtl)
holes = parse_drill(drl)

visualize("ESP32 玩偶底板", pads)
group_by_region(pads)
