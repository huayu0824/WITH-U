# Arduino IDE 编译设置

开发板：ESP32-S3-WROOM-1 N16R8（16MB Flash、8MB Octal PSRAM）。

推荐设置：

| 选项 | 值 |
|---|---|
| Board | ESP32S3 Dev Module |
| USB CDC On Boot | Enabled |
| CPU Frequency | 240MHz |
| Flash Size | 16MB |
| Flash Mode | QIO 80MHz |
| Partition Scheme | 16MB Flash 对应分区 |
| PSRAM | OPI PSRAM |
| Upload Speed | 921600；不稳定时改 460800 |

库管理器依赖：

- ArduinoJson，作者 Benoit Blanchon
- WebSockets，作者 Markus Sattler / Links2004
- WiFiManager，作者 tzapu
- ESP8266Audio，作者 Earle F. Philhower

烧录后的关键启动日志：

```text
[MEM] 缓冲区位于 PSRAM
[MEM] 录音缓冲区: 1924096 bytes
[SD] 未检测到 TF 卡
[CHAR] 使用内置默认角色（无TF卡）
[OK] INMP441
[WS] connected
```

若出现 `PSRAM 不可用，回退 DRAM`，先检查 Tools -> PSRAM 是否选择 `OPI PSRAM`。
