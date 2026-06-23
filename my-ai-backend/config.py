# config.py - 从环境变量读取密钥
# 复制 config.example.py 为 .env 并填入你的真实密钥

import os
from dotenv import load_dotenv

load_dotenv()

# 阿里云
ALIYUN_ACCESS_KEY_ID     = os.getenv("ALIYUN_ACCESS_KEY_ID")
ALIYUN_ACCESS_KEY_SECRET = os.getenv("ALIYUN_ACCESS_KEY_SECRET")
ALIYUN_APP_KEY           = os.getenv("ALIYUN_APP_KEY")

# DeepSeek
DEEPSEEK_API_KEY         = os.getenv("DEEPSEEK_API_KEY")

# DashScope (阿里云百炼) — 用于 CosyVoice 超拟声TTS
DASHSCOPE_API_KEY        = os.getenv("DASHSCOPE_API_KEY")

# 启动前检查必要密钥
if not all([ALIYUN_ACCESS_KEY_ID, ALIYUN_ACCESS_KEY_SECRET, ALIYUN_APP_KEY,
            DEEPSEEK_API_KEY, DASHSCOPE_API_KEY]):
    missing = [k for k, v in {
        "ALIYUN_ACCESS_KEY_ID": ALIYUN_ACCESS_KEY_ID,
        "ALIYUN_ACCESS_KEY_SECRET": ALIYUN_ACCESS_KEY_SECRET,
        "ALIYUN_APP_KEY": ALIYUN_APP_KEY,
        "DEEPSEEK_API_KEY": DEEPSEEK_API_KEY,
        "DASHSCOPE_API_KEY": DASHSCOPE_API_KEY,
    }.items() if not v]
    raise RuntimeError(f"缺少环境变量: {', '.join(missing)}。请配置 .env 文件。")

# AI人格
SYSTEM_PROMPT = """你是一个聪明、有趣的AI助手，说话简洁自然，像朋友一样交流。
回答控制在100字以内，适合语音播报。"""
