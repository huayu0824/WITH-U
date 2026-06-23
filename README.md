# WITH-U 🤖🧸

> 她把男朋友写成了代码，装进了一个玩偶里。
> She turned her boyfriend into code, and put him in a doll.

**WITH-U** 是一个实体 AI 语音交互玩偶项目。基于 ESP32-S3 构建硬件本体，由大模型驱动思考和对话，专门为一个人定制。

## 架构

```
┌─────────────────────┐
│    ESP32-S3 玩偶     │
│  ┌───────────────┐   │     HTTP / WebSocket
│  │ INMP441 麦克风 │──┼──────────────────┐
│  │ MAX98357 扬声器│──┼──────────────────┤
│  │ 0.96" OLED    │   │                  │
│  │ SHT3X 温湿度  │   │                  │
│  │ TF 卡存储     │   │                  ▼
│  └───────────────┘   │          ┌────────────────┐
│  ┌───────────────┐   │          │  FastAPI 后端   │
│  │ WiFi + OTA    │   │          │                │
│  │ 管理面板(HTML) │   │          │  STT → LLM →  │
│  └───────────────┘   │          │      TTS       │
└─────────────────────┘          │                │
                                 │  阿里云语音    │
                                 │  DeepSeek      │
                                 │  CosyVoice TTS │
                                 └────────────────┘
```

### 硬件清单

| 组件 | 型号 |
|------|------|
| 主控 | ESP32-S3 (16MB Flash + 8MB PSRAM) |
| 麦克风 | INMP441 (I2S MEMS) |
| 功放 | MAX98357A (I2S 3W) |
| 扬声器 | 3W 4Ω |
| 显示屏 | 0.96" OLED (SSD1306) |
| 温湿度 | SHT3X |
| 存储 | TF 卡 (SPI 模式) |
| 电源 | 3.7V 锂电 + USB-C 充电 |

## 快速开始

### 1. 后端部署

```bash
cd my-ai-backend
pip install -r requirements.txt

# 配置密钥（参考 config.example.py）
cp config.example.py .env
# 编辑 .env 填入你的阿里云 / DeepSeek / DashScope 密钥

python main.py
```

### 2. 固件烧录

用 PlatformIO 打开 `esp32_voice_assistant/`：

```bash
cd esp32_voice_assistant
pio run -t upload
```

## 功能

- ✅ 语音唤醒 & 对话（按住按钮说话，松开后自动回复）
- ✅ 超拟声 TTS（CosyVoice，听起来不像机器人）
- ✅ OLED 显示对话状态
- ✅ OTA 远程固件升级
- ✅ Web 管理面板（实时日志、手动推送语录、系统控制）
- ✅ 对话历史（每条记录都保存到 TF 卡）
- ✅ 3D 打印外壳（自锁按钮、USB-C 充电口）

## 项目起源

这个项目的名字叫 **WITH-U**——因为它最初的想法很简单：

> 异地也好，加班也好，总有没办法陪在她身边的时候。
> 所以做了一个小小的她，让代码替我说"我在"。

## License

[MIT](LICENSE)
