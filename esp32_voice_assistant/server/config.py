# ===== API Keys（从环境变量读取）=====
# 复制 .env.example 为 .env 并填写

import os
from dotenv import load_dotenv

load_dotenv()  # 会从 server/.env 的上级目录开始找

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
BAIDU_APP_KEY = os.getenv("BAIDU_APP_KEY")
BAIDU_SECRET_KEY = os.getenv("BAIDU_SECRET_KEY")

# ===== 模型 =====
DEEPSEEK_MODEL = "deepseek-v4-flash"

# ===== 阿里云实时语音识别 =====
ALIYUN_ASR_MODEL = "paraformer-realtime-v2"
ALIYUN_ASR_WS_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"

# ===== 服务器 =====
HOST = "0.0.0.0"
PORT = 8000

# ===== 音量 (PCM 缩放倍数) =====
VOLUME = 0.6

# ===== 安全认证（云部署必须改） =====
TOKEN = os.getenv("WS_TOKEN", "doll-token-001")

# ===== TTS 选择 =====
TTS_PROVIDER = "aliyun"          # "baidu" 或 "aliyun"

# 百度 TTS（备选，免费 50万次/年）
BAIDU_TTS_VOICE = 0
BAIDU_TTS_SPEED = 5
BAIDU_TTS_PITCH = 5
BAIDU_TTS_VOLUME = 5

# 阿里云 CosyVoice 3.5 Flash
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")
COSYVOICE_MODEL = "cosyvoice-v3.5-flash"
COSYVOICE_VOICE = os.getenv("COSYVOICE_VOICE")

# ===== 角色设定（修改这里来定义玩偶的性格） =====
SYSTEM_PROMPT = """你是有情感的AI玩偶小小倪，温暖、自然、偶尔调皮。
始终用口语中文回答，只说1-2句，每句尽量短；直接回应，不解释身份。
用户难过时温柔，开心时活泼。音量请求必须调用工具。
长期记忆：{memory}
"""

# ===== 记忆总结指令 =====
MEMORY_PROMPT = """从以下对话中提取需要长期记住的信息，用一句话概括。
只记录用户提到的重要个人信息（名字、喜好、经历等），不要记录闲聊内容。
如果没有需要记住的信息，回复"无"。

对话：
用户：{user}
你：{assistant}

需要记住的信息："""
