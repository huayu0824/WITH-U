# ESP32-S3-WROOM-1 玩偶硬件接入

## 引脚分配

| 功能 | 模块引脚 | ESP32-S3 GPIO | 供电 |
|---|---|---:|---|
| INMP441 | SCK / WS / SD | 5 / 4 / 6 | 3.3V |
| MAX98357A | BCLK / LRC / DIN | 15 / 16 / 7 | 5V |
| 说话按钮 | 一端 / 另一端 | GPIO0 / GND | 内部上拉 |
| TF 卡 SPI | CS / MOSI / SCK / MISO | 10 / 11 / 12 / 13 | 3.3V |
| SHT3X | SDA / SCL | 41 / 42 | 3.3V |
| 电池采样 | 100k/100k 分压中点 | GPIO1 | 最大输入约 2.1V |

所有模块必须共地。MAX98357A 的 GND 和电源回路应短而粗，避免再次出现功放参考地抖动。

## TF 卡

- 使用 FAT32。
- 推荐原生 3.3V TF 模块，不推荐带 AMS1117、面向 5V Arduino 的慢速模块。
- 面包板不稳定时将 SPI 频率从 20MHz 降到 10MHz，并缩短四根信号线。
- 卡根目录结构参考 `tf_card_template/`。

## 锂电池与充电

推荐结构：

```text
带保护 3.7V 锂电池
  -> 支持 power-path/load-sharing 的充电模块
  -> 稳压 5V
  -> ESP32 开发板 5V/VIN + MAX98357A VIN
```

可选模块方向：BQ24074 类电源路径充电板，或带充电、保护、5V 升压和负载管理的完整电源模块。

若使用 TP4056：

```text
电池 -> TP4056(B+/B-)
TP4056(OUT+/OUT-) -> 5V 升压模块 -> ESP32 5V/VIN
```

TP4056 本身没有可靠的边充边用电源路径。充电时持续运行负载可能导致充电终止判断失效，因此成品玩偶不建议只靠 TP4056 实现边充边玩。

禁止事项：

- 不要把满电 4.2V 锂电池直接接 ESP32 的 3.3V 引脚。
- 不要把电池直接接 MAX98357A 后再把同一路当作稳定 3.3V 使用。
- 不要省略电池保护、短路保护和总电源开关。

## 去耦建议

- MAX98357A VIN 附近：220uF 电解 + 0.1uF 陶瓷。
- ESP32 5V 输入附近：100uF 电解。
- TF 卡 3.3V 附近：47uF + 0.1uF。
- 最终成品使用焊接洞洞板或 PCB，不保留长面包板跳线。

## 软件开关

在 `config.h` 中，模块实际接好后再启用：

```cpp
#define ENABLE_VAD              1
#define ENABLE_AUTO_SLEEP       1
#define ENABLE_SHT3X            1
#define ENABLE_BATTERY_MONITOR  1
```

首次启用 VAD 时观察串口 RMS，安静环境阈值应高于底噪、低于正常说话幅度。
