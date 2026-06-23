"""
ESP32 语音助手服务端 (HTTP + WebSocket)
接收 PCM → 百度 REST ASR → DeepSeek LLM → 阿里云 CosyVoice TTS → 返回 PCM
"""
import base64
import json
import asyncio
import time
import re
import array
import os
import datetime
import httpx
import math
import wave
import threading
import dashscope
from contextlib import asynccontextmanager
from dashscope.audio.asr import Recognition, RecognitionCallback, RecognitionResult
from fastapi import FastAPI, File, UploadFile, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import Response, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from config import *

# ===== 运行时可变状态（语音指令可修改） =====
RUNTIME = {"volume": VOLUME}

def scale_pcm(data: bytes) -> bytes:
    """调整 16-bit PCM 音量"""
    vol = RUNTIME["volume"]
    samples = array.array('h', data)
    for i in range(len(samples)):
        samples[i] = int(samples[i] * vol)
    return samples.tobytes()

@asynccontextmanager
async def app_lifespan(_app: FastAPI):
    task = asyncio.create_task(proactive_manager())
    try:
        yield
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


app = FastAPI(lifespan=app_lifespan)
MEMORY_FILE = "memory.json"
CHARACTER_FILE = "character_profile.json"

memory_store = {}
_baidu_token = {"access_token": "", "expires_at": 0}
_active_handlers: dict[str, asyncio.Task] = {}  # 打断追踪
character_profiles: dict[str, dict] = {}
latest_sensors: dict[str, dict] = {}
_memory_lock = asyncio.Lock()
connected_clients: dict[str, WebSocket] = {}
client_last_activity: dict[str, float] = {}
client_busy: dict[str, bool] = {}
proactive_tasks: dict[str, asyncio.Task] = {}
client_listen_mode: dict[str, bool] = {}
client_listen_buffer: dict[str, bytearray] = {}

FIXED_COMMANDS = (
    {
        "keywords": ("现在几点", "几点了", "时间"),
        "file": "/audio/commands/time.pcm",
    },
    {
        "keywords": ("你在吗", "醒醒", "小智小智"),
        "file": "/audio/wake.pcm",
    },
)


def route_fixed_command(text: str) -> str:
    normalized = re.sub(r"[，。！？,.!?\s]", "", text)
    for command in FIXED_COMMANDS:
        if any(keyword in normalized for keyword in command["keywords"]):
            return command["file"]
    profile = character_profiles.get("default", {})
    for command in profile.get("commands", []) if isinstance(profile, dict) else []:
        if not isinstance(command, dict) or command.get("action") != "local_audio":
            continue
        keywords = command.get("keywords") or [command.get("phrase", "")]
        if any(str(keyword) and str(keyword) in normalized for keyword in keywords):
            filename = os.path.basename(str(command.get("file", "")))
            if filename:
                return "/audio/commands/" + filename
    return ""


def is_sleep_command(text: str) -> bool:
    normalized = re.sub(r"[，。！？,.!?\s]", "", text)
    return any(keyword in normalized for keyword in ("关机", "休息吧", "睡觉吧", "进入休眠"))


def build_system_prompt(session_id: str, memory: str) -> str:
    profile = character_profiles.get(session_id, {})
    personality = profile.get("personality", {}) if isinstance(profile, dict) else {}
    name = str(profile.get("name", "小小倪"))[:20]
    background = str(profile.get("background", "你是有情感的AI玩偶。"))[:500]
    style = str(personality.get("style", "口语中文"))[:50]
    traits = personality.get("traits", ["温暖", "自然", "偶尔调皮"])
    if not isinstance(traits, list):
        traits = ["温暖", "自然"]
    traits_text = "、".join(str(item)[:20] for item in traits[:8])
    try:
        max_sentences = max(1, min(3, int(personality.get("max_sentences", 2))))
    except (TypeError, ValueError):
        max_sentences = 2
    sensor = latest_sensors.get(session_id, {})
    sensor_text = ""
    if sensor:
        sensor_text = (
            f"\n当前传感器：温度={sensor.get('temperature_c')}°C，"
            f"湿度={sensor.get('humidity_percent')}%，"
            f"电量={sensor.get('battery_percent')}%。"
        )
    return (
        f"你叫{name}。{background}\n"
        f"性格：{traits_text}；表达风格：{style}。\n"
        f"始终直接用口语中文回答，日常对话控制在{max_sentences}句话以内，简短自然；但如果用户明确要求详细说明（如“详细说”、“多说一点”、“展开讲讲”），可以适当展开详细回答。不要解释身份。\n"
        f"你可以查天气、查时间日期、搜索百科知识、搜索网页、做数学计算。需要时会自动使用这些能力。\n"
        f"音量请求必须调用工具。长期记忆：{memory or '暂无'}{sensor_text}"
    )


def load_memory():
    global memory_store
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                memory_store = json.load(f)
            print(f"[MEM] 已加载 {len(memory_store)} 个会话记忆", flush=True)
        except (json.JSONDecodeError, OSError):
            memory_store = {}
    else:
        memory_store = {}


def save_memory():
    try:
        with open(MEMORY_FILE + ".tmp", "w", encoding="utf-8") as f:
            json.dump(memory_store, f, ensure_ascii=False, indent=2)
        os.replace(MEMORY_FILE + ".tmp", MEMORY_FILE)
    except OSError as e:
        print(f"[MEM] 保存失败: {e}", flush=True)


def save_input_debug_wav(pcm: bytes):
    """Persist the latest microphone input and print signal-level diagnostics."""
    if not pcm:
        print("[AUDIO] 输入为空", flush=True)
        return
    samples = array.array("h", pcm[:len(pcm) & ~1])
    peak = max((abs(value) for value in samples), default=0)
    rms = math.sqrt(sum(value * value for value in samples) / max(1, len(samples)))
    duration = len(samples) / 16000
    with wave.open("last_input.wav", "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(samples.tobytes())
    print(
        f"[AUDIO] {duration:.2f}s peak={peak} rms={rms:.0f} saved=last_input.wav",
        flush=True,
    )


def save_wav(pcm: bytes, filepath: str):
    """将 16-bit mono PCM 保存为 WAV 文件"""
    if not pcm:
        return
    samples = array.array("h", pcm[:len(pcm) & ~1])
    with wave.open(filepath, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(samples.tobytes())


load_memory()


def load_character():
    global character_profiles
    if os.path.exists(CHARACTER_FILE):
        try:
            with open(CHARACTER_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                character_profiles = loaded
                print(f"[CHAR] 已加载角色: {character_profiles.get('default', {}).get('name', '未命名')}", flush=True)
        except (json.JSONDecodeError, OSError) as e:
            print(f"[CHAR] 加载失败: {e}", flush=True)


def save_character():
    try:
        with open(CHARACTER_FILE + ".tmp", "w", encoding="utf-8") as f:
            json.dump(character_profiles, f, ensure_ascii=False, indent=2)
        os.replace(CHARACTER_FILE + ".tmp", CHARACTER_FILE)
    except OSError as e:
        print(f"[CHAR] 保存失败: {e}", flush=True)


load_character()


# ============================================================
# 百度 access_token
# ============================================================
async def get_baidu_token() -> str:
    if time.time() < _baidu_token["expires_at"]:
        return _baidu_token["access_token"]

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            "https://aip.baidubce.com/oauth/2.0/token",
            params={
                "grant_type": "client_credentials",
                "client_id": BAIDU_APP_KEY,
                "client_secret": BAIDU_SECRET_KEY,
            }
        )
    if resp.status_code != 200:
        print(f"[BAIDU] token 失败: {resp.text}")
        return ""

    data = resp.json()
    _baidu_token["access_token"] = data["access_token"]
    _baidu_token["expires_at"] = time.time() + data.get("expires_in", 2592000) - 3600
    return _baidu_token["access_token"]


# ============================================================
# 百度流式 ASR — 当前使用 REST API 主力
# ============================================================


# ============================================================
# 阿里云实时 ASR
# ============================================================
class _ASRCallback(RecognitionCallback):
    def __init__(self, owner):
        self.owner = owner

    def on_open(self):
        print("[ASR-DBG] on_open - 连接已建立", flush=True)

    def on_complete(self):
        print("[ASR-DBG] on_complete - 识别完成", flush=True)

    def on_close(self):
        print("[ASR-DBG] on_close - 连接关闭", flush=True)

    def on_event(self, result: RecognitionResult):
        sentence = result.get_sentence()
        print(f"[ASR-DBG] on_event 触发 sentence={type(sentence).__name__}", flush=True)
        sentences = sentence if isinstance(sentence, list) else [sentence]
        with self.owner.lock:
            for item in sentences:
                if not isinstance(item, dict):
                    print(f"[ASR-DBG] 非dict: {type(item)}", flush=True)
                    continue
                text = item.get("text", "").strip()
                print(f"[ASR-DBG] text='{text}' end={item.get('sentence_end')}", flush=True)
                if not text:
                    continue
                if RecognitionResult.is_sentence_end(item):
                    self.owner.final_parts.append(text)
                    self.owner.partial = ""
                else:
                    self.owner.partial = text

    def on_error(self, result: RecognitionResult):
        self.owner.error = f"{result.code or result.status_code}: {result.message}"
        print(f"[ASR-ERR] {self.owner.error}", flush=True)


class AliyunRealtimeASR:
    """Async adapter around the official DashScope realtime ASR SDK."""

    def __init__(self):
        self.recognition = None
        self.final_parts: list[str] = []
        self.partial = ""
        self.error = ""
        self.lock = threading.Lock()
        self.started = False

    async def start(self):
        if not DASHSCOPE_API_KEY:
            raise RuntimeError("DASHSCOPE_API_KEY 未配置")
        dashscope.api_key = DASHSCOPE_API_KEY
        self.recognition = Recognition(
            model=ALIYUN_ASR_MODEL,
            callback=_ASRCallback(self),
            format="pcm",
            sample_rate=16000,
        )
        await asyncio.to_thread(self.recognition.start)
        self.started = True
        print(f"[ASR] 官方SDK实时识别已启动 model={ALIYUN_ASR_MODEL}", flush=True)

    async def send_audio(self, pcm: bytes):
        if self.started and pcm:
            self.recognition.send_audio_frame(pcm)

    async def finish(self) -> str:
        if not self.started:
            return ""
        try:
            await asyncio.wait_for(asyncio.to_thread(self.recognition.stop), timeout=15)
        except asyncio.TimeoutError:
            self.error = "等待最终识别结果超时"
        finally:
            self.started = False

        with self.lock:
            text = "".join(self.final_parts)
            if self.partial and self.partial not in text:
                text += self.partial
        text = text.strip()
        if self.error:
            print(f"[ASR] 错误: {self.error}", flush=True)
        elif text:
            print(f"[ASR] {text}", flush=True)
        else:
            print("[ASR] 结果为空", flush=True)
        return text

    async def close(self):
        if self.started and self.recognition:
            try:
                await asyncio.wait_for(asyncio.to_thread(self.recognition.stop), timeout=5)
            except Exception:
                pass
            self.started = False


async def transcribe_pcm(pcm: bytes) -> str:
    """HTTP compatibility path; WebSocket clients stream directly instead."""
    # 百度 REST ASR（主力）
    text = await baidu_transcribe(pcm)
    if text:
        return text
    # 备选：阿里云 ASR
    print("[ASR] 百度无结果，尝试阿里云 ASR...", flush=True)
    session = AliyunRealtimeASR()
    try:
        await session.start()
        for offset in range(0, len(pcm), 3200):
            await session.send_audio(pcm[offset:offset + 3200])
        return await session.finish()
    except Exception as e:
        print(f"[ASR] 阿里云失败: {e}", flush=True)
        return ""
    finally:
        await session.close()


async def baidu_transcribe(pcm: bytes) -> str:
    """百度语音识别 (REST API)"""
    if not pcm or not BAIDU_APP_KEY or not BAIDU_SECRET_KEY:
        return ""
    try:
        token = await get_baidu_token()
    except httpx.HTTPError as error:
        print(f"[BAIDU-ASR] token 请求失败: {error}", flush=True)
        return ""
    if not token:
        print("[BAIDU-ASR] token 获取失败", flush=True)
        return ""

    # 包 WAV 头
    wav_bytes = await asyncio.to_thread(_make_wav, pcm)
    speech_b64 = base64.b64encode(wav_bytes).decode()
    speech_len = len(wav_bytes)

    payload = {
        "format": "wav",
        "rate": 16000,
        "channel": 1,
        "cuid": "esp32_doll",
        "token": token,
        "speech": speech_b64,
        "len": speech_len,
    }
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(
                    "https://vop.baidu.com/server_api", json=payload
                )
            if resp.status_code != 200:
                print(f"[BAIDU-ASR] HTTP {resp.status_code}: {resp.text[:200]}", flush=True)
            else:
                data = resp.json()
                if data.get("err_no", -1) == 0:
                    text = "".join(data.get("result", [])).strip()
                    if text:
                        print(f"[BAIDU-ASR] {text}", flush=True)
                        return text
                print(
                    f"[BAIDU-ASR] 错误 {data.get('err_no')}: {data.get('err_msg', '')}",
                    flush=True,
                )
        except (httpx.HTTPError, ValueError) as error:
            print(f"[BAIDU-ASR] 请求异常: {error}", flush=True)
        if attempt == 0:
            await asyncio.sleep(0.4)
    return ""


def _make_wav(pcm: bytes) -> bytes:
    """包 WAV 头 (16kHz 16-bit mono)"""
    import struct
    data_len = len(pcm)
    buf = bytearray(44 + data_len)
    # RIFF header
    buf[0:4] = b'RIFF'
    struct.pack_into('<I', buf, 4, 36 + data_len)
    buf[8:12] = b'WAVE'
    # fmt chunk
    buf[12:16] = b'fmt '
    struct.pack_into('<I', buf, 16, 16)  # chunk size
    struct.pack_into('<H', buf, 20, 1)   # PCM
    struct.pack_into('<H', buf, 22, 1)   # mono
    struct.pack_into('<I', buf, 24, 16000)
    struct.pack_into('<I', buf, 28, 32000)  # byte rate
    struct.pack_into('<H', buf, 32, 2)   # block align
    struct.pack_into('<H', buf, 34, 16)  # bits per sample
    # data chunk
    buf[36:40] = b'data'
    struct.pack_into('<I', buf, 40, data_len)
    buf[44:44+data_len] = pcm
    return bytes(buf)


# ============================================================
# LLM
# ============================================================
async def chat(text: str, session_id: str) -> str:
    if not DEEPSEEK_API_KEY:
        return "我还没设置API密钥呢。"

    mem = memory_store.get(session_id, {"memory": "", "history": []})
    system_prompt = build_system_prompt(session_id, mem["memory"])

    messages = [{"role": "system", "content": system_prompt}]
    for role, content in mem["history"][-2:]:
        messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": text})

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.deepseek.com/chat/completions",
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": DEEPSEEK_MODEL,
                "messages": messages,
                "temperature": 0.8, "max_tokens": 300
            }
        )

    if resp.status_code != 200:
        print(f"[LLM] 错误 {resp.status_code}")
        return "嗯，我脑袋卡了一下，你再说一遍？"

    reply = resp.json()["choices"][0]["message"]["content"].strip()
    print(f"[LLM] {reply}")
    return reply


# ===== LLM 工具定义（LLM 通过 function calling 调用） =====
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "set_volume",
            "description": "用户要求调节音量时，必须调用此工具，不要用文字回复音量相关请求。",
            "parameters": {
                "type": "object",
                "properties": {
                    "level": {
                        "type": "number",
                        "description": "目标音量，0.0(静音)~1.0(最大)。用户说百分比时除以100，例如50%→0.5",
                        "minimum": 0.0,
                        "maximum": 1.0
                    }
                },
                "required": ["level"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "查询某个城市的实时天气。用户问天气、温度、下雨、刮风、湿度时调用。自动识别城市名。",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "城市名称，如'广州'、'北京'、'上海'"
                    }
                },
                "required": ["city"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_datetime",
            "description": "查询当前的日期、时间、星期。用户问'现在几点'、'今天几号'、'星期几'时调用。",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_baike",
            "description": "在百科搜索查询知识信息。用户问'什么是XX'、'介绍一下XX'、'XX是什么意思'时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "要查询的词条名称"
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "搜索互联网获取最新信息。用户问新闻、实时动态或需要联网搜索的问题时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词"
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "calculate",
            "description": "执行数学计算。用户问算术题、数学运算时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "数学表达式，如 '256*78'、'(15+8)*3'"
                    }
                },
                "required": ["expression"]
            }
        }
    }
]


def should_enable_tools(text: str) -> bool:
    return True  # 始终开启工具，LLM 会自动判断是否需要调用


# ============================================================
# 工具函数实现
# ============================================================
async def tool_get_weather(city: str) -> str:
    """查询实时天气 (wttr.in)"""
    if not city:
        return "请告诉我城市名"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"https://wttr.in/{city}?format=j1")
        if resp.status_code != 200:
            return f"{city}天气查询暂时不可用"
        data = resp.json()
        current = data["current_condition"][0]
        temp = current["temp_C"]
        humidity = current["humidity"]
        desc = current["weatherDesc"][0]["value"]
        wind = current["windspeedKmph"]
        feels = current["FeelsLikeC"]
        return f"{city}当前天气：{desc}，气温{temp}°C（体感{feels}°C），湿度{humidity}%，风速{wind}km/h"
    except Exception as e:
        return f"天气查询失败: {e}"


def tool_get_datetime() -> str:
    """获取当前日期时间"""
    import datetime
    now = datetime.datetime.now()
    weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    weekday = weekdays[now.weekday()]
    return f"现在是{now.year}年{now.month}月{now.day}日 {weekday} {now.hour:02d}:{now.minute:02d}"


async def tool_search_baike(query: str) -> str:
    """百科搜索（百度百科 + ownthink 知识图谱）"""
    if not query:
        return "请告诉我搜索什么"
    try:
        # 方式1: 百度百科开放 API
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://baike.baidu.com/api/openapi/BaikeLemmaCardApi",
                params={"scope": "103", "format": "json", "appid": "379020", "bk_key": query}
            )
        if resp.status_code == 200:
            data = resp.json()
            title = data.get("key") or data.get("title", "")
            desc = data.get("desc", "")
            if title and desc:
                return f"百度百科 · {title}：{desc[:300]}"

        # 方式2: ownthink 知识图谱（备选）
        async with httpx.AsyncClient(timeout=10) as client:
            resp2 = await client.get(
                "https://api.ownthink.com/kg/knowledge",
                params={"entity": query}
            )
        if resp2.status_code == 200:
            data2 = resp2.json()
            if data2.get("message") == "success" and data2.get("data"):
                entity = data2["data"].get("entity", query)
                desc = data2["data"].get("desc", "")
                if desc:
                    return f"{entity}：{desc[:300]}"

        return f"没有找到'{query}'的相关信息"
    except Exception as e:
        return f"百科查询失败: {e}"


async def tool_search_web(query: str) -> str:
    """网页搜索（Bing CN）"""
    if not query:
        return "请告诉我搜索什么"
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(
                "https://cn.bing.com/search",
                params={"q": query, "count": 3},
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            )
        if resp.status_code == 200:
            import re
            # 提取搜索结果摘要
            results = []
            # 查找所有 class 包含 b_caption 或 b_algo 的区块
            snippets = re.findall(
                r'<h2><a[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?<p[^>]*>(.*?)</p>',
                resp.text, re.DOTALL
            )
            for href, title, snippet in snippets[:3]:
                clean_title = re.sub(r'<[^>]+>', '', title).strip()
                clean_snippet = re.sub(r'<[^>]+>', '', snippet).strip()
                if clean_title and clean_snippet:
                    results.append(f"{clean_title}：{clean_snippet[:150]}")
            if results:
                return "\n".join(results[:3])
            # 备选：搜狗
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client2:
                resp2 = await client2.get(
                    "https://www.sogou.com/web",
                    params={"query": query},
                    headers={"User-Agent": "Mozilla/5.0"}
                )
            if resp2.status_code == 200:
                snippets2 = re.findall(
                    r'<h3[^>]*>.*?<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?</h3>',
                    resp2.text, re.DOTALL
                )
                texts = []
                for href, title in snippets2[:3]:
                    t = re.sub(r'<[^>]+>', '', title).strip()
                    if t:
                        texts.append(t)
                if texts:
                    return " | ".join(texts)
            return f"关于'{query}'的搜索结果未找到"
    except Exception as e:
        return f"搜索失败: {e}"


def safe_calculate(expression: str) -> str:
    """安全执行数学计算"""
    if not expression:
        return "请告诉我算式"
    import ast, operator
    allowed_ops = {
        ast.Add: operator.add, ast.Sub: operator.sub,
        ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.USub: operator.neg,
        ast.Mod: operator.mod, ast.FloorDiv: operator.floordiv,
    }
    def eval_node(node):
        if isinstance(node, ast.Expression):
            return eval_node(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)):
                return node.value
            raise ValueError(f"unsupported constant: {type(node.value)}")
        if isinstance(node, ast.UnaryOp):
            op_fn = allowed_ops.get(type(node.op))
            if not op_fn:
                raise ValueError(f"unsupported unary op: {type(node.op).__name__}")
            return op_fn(eval_node(node.operand))
        if isinstance(node, ast.BinOp):
            op_fn = allowed_ops.get(type(node.op))
            if not op_fn:
                raise ValueError(f"unsupported bin op: {type(node.op).__name__}")
            return op_fn(eval_node(node.left), eval_node(node.right))
        raise ValueError(f"unsupported: {type(node).__name__}")
    try:
        tree = ast.parse(expression.strip(), mode="eval")
        result = eval_node(tree.body)
        # 处理浮点显示
        if isinstance(result, float):
            if result == int(result):
                result = int(result)
            else:
                result = round(result, 6)
        return f"{expression} = {result}"
    except Exception as e:
        return f"计算不了这个: {e}"


# ============================================================
# LLM 流式（SSE → delta 生成器，支持 function calling）
# ============================================================
async def chat_stream(text: str, session_id: str):
    """Generator: yield text deltas from DeepSeek SSE stream.
    内部处理 tool calls，调用方只收到最终文本。
    """
    if not DEEPSEEK_API_KEY:
        yield "我还没设置API密钥呢。"
        return

    mem = memory_store.get(session_id, {"memory": "", "history": []})
    system_prompt = build_system_prompt(session_id, mem["memory"])

    messages = [{"role": "system", "content": system_prompt}]
    for role, content in mem["history"][-2:]:
        messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": text})

    async with httpx.AsyncClient(timeout=60) as client:
        while True:
            request_json = {
                "model": DEEPSEEK_MODEL,
                "messages": messages,
                "temperature": 0.7,
                "max_tokens": 300,
                "stream": True
            }
            if should_enable_tools(text):
                request_json["tools"] = TOOLS
                request_json["tool_choice"] = "auto"

            async with client.stream(
                "POST", "https://api.deepseek.com/chat/completions",
                headers={
                    "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                    "Content-Type": "application/json"
                },
                json=request_json
            ) as resp:
                if resp.status_code != 200:
                    print(f"[LLM] 错误 {resp.status_code}")
                    yield "嗯，我脑袋卡了一下，你再说一遍？"
                    return

                # 累积本轮响应
                content = ""
                tool_calls: dict[int, dict] = {}
                finish_reason = None

                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    if line.startswith("data:") and line.strip() != "data: [DONE]":
                        try:
                            chunk = json.loads(line[5:])
                            choice = chunk.get("choices", [{}])[0]
                            delta = choice.get("delta", {})
                            finish = choice.get("finish_reason")
                            if finish:
                                finish_reason = finish

                            # 文本 delta
                            if delta.get("content"):
                                content += delta["content"]
                                yield delta["content"]

                            # tool call delta
                            if delta.get("tool_calls"):
                                for tc in delta["tool_calls"]:
                                    idx = tc["index"]
                                    if idx not in tool_calls:
                                        tool_calls[idx] = {"id": "", "name": "", "args": ""}
                                    if tc.get("id"):
                                        tool_calls[idx]["id"] = tc["id"]
                                    if tc.get("function", {}).get("name"):
                                        tool_calls[idx]["name"] += tc["function"]["name"]
                                    if tc.get("function", {}).get("arguments"):
                                        tool_calls[idx]["args"] += tc["function"]["arguments"]
                        except (json.JSONDecodeError, KeyError, IndexError):
                            pass

                # 处理 tool calls
                if finish_reason == "tool_calls" and tool_calls:
                    # 把 assistant 的 tool_calls 加入历史
                    assistant_msg = {"role": "assistant", "content": content or None}
                    assistant_msg["tool_calls"] = [
                        {"id": tc["id"], "type": "function",
                         "function": {"name": tc["name"], "arguments": tc["args"]}}
                        for tc in tool_calls.values()
                    ]
                    messages.append(assistant_msg)

                    # 执行每个 tool
                    for tc in tool_calls.values():
                        fn_name = tc["name"]
                        result = ""
                        try:
                            args = json.loads(tc["args"])
                            if fn_name == "set_volume":
                                RUNTIME["volume"] = max(0.0, min(1.0, args["level"]))
                                result = f"ok, volume={args['level']}"
                                print(f"[TOOL] set_volume({args['level']})", flush=True)
                            elif fn_name == "get_weather":
                                result = await tool_get_weather(args.get("city", ""))
                                print(f"[TOOL] get_weather({args.get('city')}) → {result[:60]}", flush=True)
                            elif fn_name == "get_datetime":
                                result = tool_get_datetime()
                                print(f"[TOOL] get_datetime → {result}", flush=True)
                            elif fn_name == "search_baike":
                                result = await tool_search_baike(args.get("query", ""))
                                print(f"[TOOL] search_baike({args.get('query')})", flush=True)
                            elif fn_name == "search_web":
                                result = await tool_search_web(args.get("query", ""))
                                print(f"[TOOL] search_web({args.get('query')})", flush=True)
                            elif fn_name == "calculate":
                                result = safe_calculate(args.get("expression", ""))
                                print(f"[TOOL] calculate → {result}", flush=True)
                            else:
                                result = f"unknown tool: {fn_name}"
                        except Exception as e:
                            result = f"error: {e}"

                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": result
                        })

                    # continue → 下一轮 LLM 调用（用 tool 结果生成自然语言回复）
                    continue
                else:
                    # 没有 tool call，正常结束
                    return


# ============================================================
# 句子分割（流式 LLM → 逐句 TTS）
# ============================================================
def extract_complete_sentences(
    buffer: str, aggressive: bool = False, min_len: int = 8, first_chunk_chars: int = 18
):
    """从流式文本缓冲中提取完整句子。
    只在句号、叹号、问号、换行处分句，避免逗号分句造成结巴。
    aggressive=True: 首句模式，在积累够 first_chunk_chars 字符后优先出句。
    Returns (sentences, remaining_buffer)
    """
    # 仅在句子结束处分句，不在逗号处分——避免"今天天气很好啊，（等1.5s）适合出去玩"
    pattern = r'[。！？.!?\n]'
    strong_pattern = r'[。！？.!?\n]'

    sentences = []
    remaining = buffer

    while remaining:
        m = re.search(pattern, remaining)
        if m is None:
            # 没有完整句子：首句模式下积累够字符就强制出
            if aggressive and len(remaining.strip()) >= first_chunk_chars:
                # 在最后标点处切，如果没有则在 first_chunk_chars 处切
                last_punct = max(
                    remaining.rfind(c) for c in "。！？.!?，,；;"
                )
                if last_punct >= first_chunk_chars // 2:
                    candidate = remaining[:last_punct + 1].strip()
                    if len(candidate) >= min_len:
                        sentences.append(candidate)
                        remaining = remaining[last_punct + 1:]
                        break
                # 强制按字符数切
                candidate = remaining[:first_chunk_chars].strip()
                if candidate:
                    sentences.append(candidate)
                    remaining = remaining[first_chunk_chars:]
            break

        end = m.end()
        candidate = remaining[:end].strip()
        if len(candidate) >= min_len:
            sentences.append(candidate)
            remaining = remaining[end:]
        elif aggressive and len(remaining) >= first_chunk_chars + min_len:
            # 首句模式下，如果这段太短但已经等了不少字，强制切
            candidate = remaining[:end].strip()
            if candidate:
                sentences.append(candidate)
                remaining = remaining[end:]
            else:
                break
        else:
            break

    return sentences, remaining


# ============================================================
# 记忆更新
# ============================================================
async def _memory_llm(prompt: str, max_tokens: int = 180) -> str:
    if not DEEPSEEK_API_KEY:
        return ""
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                "https://api.deepseek.com/chat/completions",
                headers={
                    "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": DEEPSEEK_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": max_tokens,
                    "temperature": 0.2,
                },
            )
        if response.status_code == 200:
            return response.json()["choices"][0]["message"]["content"].strip()
        print(f"[MEM] LLM错误 {response.status_code}", flush=True)
    except (httpx.HTTPError, KeyError, ValueError) as error:
        print(f"[MEM] 更新异常: {error}", flush=True)
    return ""


async def update_memory(
    session_id: str, user_text: str, assistant_text: str, ws: WebSocket = None
):
    async with _memory_lock:
        mem = memory_store.get(session_id, {"memory": "", "history": []})
        mem.setdefault("memory", "")
        mem.setdefault("history", [])
        mem["history"].append(("user", user_text))
        mem["history"].append(("assistant", assistant_text))

        fact_prompt = MEMORY_PROMPT.replace("{user}", user_text).replace(
            "{assistant}", assistant_text
        )
        new_info = await _memory_llm(fact_prompt, 100)
        if new_info and new_info != "无":
            mem["memory"] = (
                f"{mem['memory']}；{new_info}" if mem["memory"] else new_info
            )
            print(f"[MEM] 新信息: {new_info}", flush=True)

        # Keep 20 rounds locally; fold the oldest 10 rounds into long-term memory.
        if len(mem["history"]) > 40:
            old_messages = mem["history"][:20]
            old_text = "\n".join(f"{role}: {content}" for role, content in old_messages)
            summary_prompt = (
                "把下面旧对话中值得长期记住的用户事实合并进已有记忆。"
                "去重、只保留事实，200字以内。\n"
                f"已有记忆：{mem['memory'] or '暂无'}\n旧对话：\n{old_text}"
            )
            summary = await _memory_llm(summary_prompt, 220)
            if summary:
                mem["memory"] = summary
            del mem["history"][:20]
            print("[MEM] 已将最早10轮归纳为长期记忆", flush=True)

        if len(mem["memory"]) > 500:
            compressed = await _memory_llm(
                "将以下长期记忆去重压缩到400字以内，保留姓名、喜好、关系和重要经历：\n"
                + mem["memory"],
                300,
            )
            if compressed:
                mem["memory"] = compressed
                print("[MEM] 长期记忆已压缩", flush=True)

        memory_store[session_id] = mem
        await asyncio.to_thread(save_memory)
        snapshot = json.dumps(mem, ensure_ascii=False, separators=(",", ":"))

    if ws:
        try:
            await ws.send_text("MEM:" + snapshot)
        except (WebSocketDisconnect, RuntimeError):
            pass


# ============================================================
# TTS: 阿里云 CosyVoice 流式（SSE → 音频块生成器）
# ============================================================
async def aliyun_tts_stream(text: str, retry: bool = True):
    """生成器，逐个 yield PCM 块（CosyVoice v3.5 Flash），失败自动重试一次"""
    if not DASHSCOPE_API_KEY:
        return

    payload = {
        "model": COSYVOICE_MODEL,
        "input": {
            "text": text,
            "voice": COSYVOICE_VOICE,
            "format": "pcm",
            "sample_rate": 16000
        }
    }
    headers = {
        "Authorization": f"Bearer {DASHSCOPE_API_KEY}",
        "Content-Type": "application/json",
        "X-DashScope-SSE": "enable"
    }

    for attempt in range(2 if retry else 1):
        chunks = []
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                async with client.stream(
                    "POST", "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/SpeechSynthesizer",
                    json=payload, headers=headers
                ) as resp:
                    if resp.status_code != 200:
                        body = await resp.aread()
                        print(f"[TTS] 错误 {resp.status_code} (尝试 {attempt+1}/2): {body[:200]}", flush=True)
                        if attempt == 0:
                            await asyncio.sleep(0.5)
                        continue

                    async for line in resp.aiter_lines():
                        if line.startswith("data:"):
                            try:
                                data = json.loads(line[5:])
                                audio_b64 = data.get("output", {}).get("audio", {}).get("data")
                                if audio_b64:
                                    chunk = base64.b64decode(audio_b64)
                                    chunks.append(chunk)
                                    yield chunk
                            except json.JSONDecodeError:
                                pass

            if chunks:
                return  # 成功收到音频，退出
            else:
                print(f"[TTS] 返回空音频 (尝试 {attempt+1}/2): \"{text[:20]}...\"", flush=True)
                if attempt == 0:
                    await asyncio.sleep(0.5)

        except (httpx.TimeoutException, httpx.ConnectError) as e:
            print(f"[TTS] 网络错误 (尝试 {attempt+1}/2): {e}", flush=True)
            if attempt == 0:
                await asyncio.sleep(0.5)

    if retry:
        print(f"[TTS] 重试耗尽，放弃: \"{text[:20]}...\"", flush=True)


# ============================================================
# TTS: 百度（备选）
# ============================================================
async def baidu_tts(text: str) -> bytes:
    """百度 TTS 非流式，返回 PCM bytes"""
    token = await get_baidu_token()
    if not token:
        return b""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://tsn.baidu.com/text2audio",
            data={"tex": text, "tok": token, "cuid": "esp32_doll", "ctp": 1, "lan": "zh",
                  "spd": BAIDU_TTS_SPEED, "pit": BAIDU_TTS_PITCH,
                  "vol": BAIDU_TTS_VOLUME, "per": BAIDU_TTS_VOICE, "aue": 4}
        )
    if resp.headers.get("content-type", "").startswith("audio/"):
        return resp.content
    return b""


async def baidu_tts_stream(text: str, retry: bool = True):
    """百度 TTS 流式生成器，逐块 yield PCM"""
    for attempt in range(2 if retry else 1):
        data = await baidu_tts(text)
        if data:
            for i in range(0, len(data), 4096):
                yield data[i:i+4096]
            return
        print(f"[TTS] 百度错误或空音频 (尝试 {attempt+1}/2): \"{text[:20]}...\"", flush=True)
        if attempt == 0:
            await asyncio.sleep(0.5)
    if retry:
        print(f"[TTS] 百度重试耗尽，放弃: \"{text[:20]}...\"", flush=True)


async def tts_stream(text: str, retry: bool = True):
    """统一 TTS 流式分发，根据 TTS_PROVIDER 选择后端"""
    if TTS_PROVIDER == "baidu":
        async for chunk in baidu_tts_stream(text, retry):
            yield chunk
    else:
        async for chunk in aliyun_tts_stream(text, retry):
            yield chunk


class PcmSendPacer:
    def __init__(self, lead_seconds: float = 0.4):
        self.started = 0.0
        self.sent_bytes = 0
        self.lead_seconds = lead_seconds

    async def send(self, ws: WebSocket, pcm: bytes) -> int:
        chunks_sent = 0
        for offset in range(0, len(pcm), 2048):
            part = pcm[offset:offset + 2048]
            if not self.started:
                self.started = time.monotonic()
            self.sent_bytes += len(part)
            target = self.sent_bytes / 32000 - self.lead_seconds
            wait = target - (time.monotonic() - self.started)
            if wait > 0:
                await asyncio.sleep(wait)
            await ws.send_bytes(part)
            chunks_sent += 1
        return chunks_sent


async def proactive_speak(client_key: str, ws: WebSocket):
    client_busy[client_key] = True
    try:
        mem = memory_store.get("default", {"memory": ""})
        hour = time.localtime().tm_hour
        period = "早上" if hour < 11 else "下午" if hour < 18 else "晚上"
        prompt = (
            f"现在是{period}，用户已经安静了一会儿。结合长期记忆主动说一句自然、"
            "不打扰人的短话；不要说明这是主动问候。"
            f"长期记忆：{mem.get('memory', '') or '暂无'}"
        )
        reply = await chat(prompt, "default")
        pacer = PcmSendPacer()
        async for pcm in tts_stream(reply):
            await pacer.send(ws, scale_pcm(pcm))
        await ws.send_text("DONE")
        print(f"[PROACTIVE] {reply}", flush=True)
    except asyncio.CancelledError:
        print(f"[PROACTIVE] 用户开始说话，已取消", flush=True)
        raise
    except (WebSocketDisconnect, RuntimeError, OSError) as error:
        print(f"[PROACTIVE] 发送失败: {error}", flush=True)
    finally:
        client_busy[client_key] = False
        client_last_activity[client_key] = time.monotonic()
        proactive_tasks.pop(client_key, None)


async def proactive_manager():
    while True:
        await asyncio.sleep(10)
        now = time.monotonic()
        for client_key, ws in list(connected_clients.items()):
            if client_busy.get(client_key, False):
                continue
            profile = character_profiles.get("default", {})
            behaviors = profile.get("behaviors", {}) if isinstance(profile, dict) else {}
            try:
                interval_min = float(behaviors.get("proactive_interval_min", 5))
            except (TypeError, ValueError):
                interval_min = 5
            if interval_min <= 0 or now - client_last_activity.get(client_key, now) < interval_min * 60:
                continue
            task = proactive_tasks.get(client_key)
            if not task or task.done():
                proactive_tasks[client_key] = asyncio.create_task(
                    proactive_speak(client_key, ws)
                )


# ============================================================
# HTTP 端点（兼容旧版）
# ============================================================
@app.post("/chat")
async def chat_endpoint(audio: UploadFile = File(...)):
    audio_bytes = await audio.read()
    print(f"\n[HTTP] 收到 {len(audio_bytes)} bytes")

    pcm = audio_bytes
    if audio_bytes.startswith(b"RIFF"):
        data_pos = audio_bytes.find(b"data")
        if data_pos >= 0 and data_pos + 8 <= len(audio_bytes):
            pcm = audio_bytes[data_pos + 8:]

    text = await transcribe_pcm(pcm)
    if not text:
        parts = [chunk async for chunk in tts_stream("我没听清，再说一遍？")]
        return Response(content=b"".join(parts), media_type="audio/pcm")

    reply = await chat(text, "default")
    asyncio.create_task(update_memory("default", text, reply))

    # 非流式：等全部 TTS 完成再返回
    pcm_parts = []
    async for chunk in tts_stream(reply):
        pcm_parts.append(chunk)

    return Response(content=b"".join(pcm_parts), media_type="audio/pcm")


# ============================================================
# 远程监听端点（HTTP 触发）
# ============================================================
@app.post("/listen/start")
async def listen_start():
    """外部调用：让玩偶开启远程拾音，PCM 流回服务器存档"""
    if not connected_clients:
        return {"status": "error", "message": "没有已连接的玩偶"}
    for key, ws in connected_clients.items():
        client_listen_mode[key] = True
        client_listen_buffer[key] = bytearray()
        client_busy[key] = True  # 阻止主动问候
        try:
            await ws.send_text("LISTEN_START")
        except Exception as e:
            client_listen_mode[key] = False
            print(f"[LISTEN] 发送失败: {e}", flush=True)
            return {"status": "error", "message": str(e)}
        print(f"[LISTEN] 🔴 远程拾音已开启: {key}", flush=True)
    return {"status": "listening"}


@app.post("/listen/stop")
async def listen_stop():
    """外部调用：停止远程拾音"""
    count = 0
    for key in list(connected_clients.keys()):
        if client_listen_mode.get(key, False):
            client_listen_mode[key] = False
            count += 1
            buffer = client_listen_buffer.pop(key, bytearray())
            if buffer:
                ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                fname = f"listen_recordings/{ts}_{key}.wav"
                os.makedirs("listen_recordings", exist_ok=True)
                save_wav(bytes(buffer), fname)
                duration = len(buffer) / 32000
                print(
                    f"[LISTEN] ✅ 录音已存档: {fname} ({len(buffer)} bytes, {duration:.1f}s)",
                    flush=True,
                )
            try:
                await connected_clients[key].send_text("LISTEN_STOP")
            except Exception:
                pass
            client_busy[key] = False
    if count:
        print(f"[LISTEN] ⏹️ 远程拾音已停止 ({count} 个客户端)", flush=True)
    else:
        return {"status": "error", "message": "监听未开启"}
    return {"status": "stopped"}


@app.get("/listen/recordings")
async def listen_recordings():
    """列出所有远程拾音录音"""
    rec_dir = "listen_recordings"
    if not os.path.exists(rec_dir):
        return {"recordings": []}
    files = []
    for f in sorted(os.listdir(rec_dir), reverse=True):
        if f.endswith(".wav"):
            path = os.path.join(rec_dir, f)
            size = os.path.getsize(path)
            duration = size / 32000
            files.append({"file": f, "size": size, "duration_s": round(duration, 1)})
    return {"recordings": files, "count": len(files)}


# ============================================================
# API：对话历史与状态
# ============================================================
if os.path.exists("listen_recordings"):
    app.mount("/audio", StaticFiles(directory="listen_recordings"), name="audio")

# ===== Firmware OTA endpoint =====
FIRMWARE_DIR = os.path.join(os.path.dirname(__file__), "firmware")
os.makedirs(FIRMWARE_DIR, exist_ok=True)
app.mount("/firmware", StaticFiles(directory=FIRMWARE_DIR), name="firmware")


@app.get("/firmware/manifest.json")
async def firmware_manifest():
    """Return OTA manifest for ESP32: latest version + download URL."""
    import glob
    import json
    firmware_files = sorted(glob.glob(os.path.join(FIRMWARE_DIR, "*.bin")), reverse=True)
    if not firmware_files:
        # Fallback: return current version as latest
        return {
            "version": "1.0.0",
            "url": "",
            "md5": "",
            "size": 0,
        }
    latest = firmware_files[0]
    fname = os.path.basename(latest)
    fsize = os.path.getsize(latest)
    # Try to compute MD5
    import hashlib
    md5 = ""
    try:
        with open(latest, "rb") as f:
            md5 = hashlib.md5(f.read()).hexdigest()
    except Exception:
        pass
    # Parse version from filename: firmware_v{N}.{N}.{N}.bin or just use filename stem
    import re
    ver_match = re.search(r"v(\d+\.\d+\.\d+)", fname)
    version = ver_match.group(1) if ver_match else fname.replace(".bin", "")
    return {
        "version": version,
        "url": f"/firmware/{fname}",
        "md5": md5,
        "size": fsize,
    }


@app.get("/api/status")
async def api_status():
    """服务器状态"""
    return {
        "clients": len(connected_clients),
        "listening": any(client_listen_mode.values()),
        "memory_size": len(memory_store.get("default", {}).get("memory", "")),
        "conversation_count": len(memory_store.get("default", {}).get("history", [])) // 2,
        "recording_count": len([f for f in os.listdir("listen_recordings") if f.endswith(".wav")])
            if os.path.exists("listen_recordings") else 0,
        "status": "running"
    }


@app.get("/api/history")
async def api_history(limit: int = 30):
    """获取对话历史"""
    mem = memory_store.get("default", {"memory": "", "history": []})
    history = mem.get("history", [])
    conversations = []
    for i in range(0, len(history), 2):
        if i + 1 < len(history):
            conversations.append({
                "user": history[i][1] if len(history[i]) > 1 else "",
                "assistant": history[i + 1][1] if len(history[i + 1]) > 1 else "",
            })
    return {
        "memory": mem.get("memory", ""),
        "conversations": conversations[-limit:],
        "total": len(conversations)
    }


# ============================================================
# API：设备配置（管理面板使用）
# ============================================================
@app.get("/api/config")
async def get_config():
    """获取完整配置（角色、运行时、记忆、系统）"""
    profile = character_profiles.get("default", {})
    mem = memory_store.get("default", {"memory": "", "history": []})
    return {
        "character": {
            "name": profile.get("name", "小小倪"),
            "background": profile.get("background", "你是有情感的AI玩偶。"),
            "personality": profile.get("personality", {
                "style": "口语中文",
                "traits": ["温暖", "自然", "偶尔调皮"],
                "max_sentences": 2,
            }),
            "behaviors": profile.get("behaviors", {
                "proactive_interval_min": 5,
            }),
        },
        "runtime": {
            "volume": RUNTIME.get("volume", 0.6),
        },
        "memory": {
            "long_term": mem.get("memory", ""),
            "history_count": len(mem.get("history", [])) // 2,
        },
        "system": {
            "tts_provider": TTS_PROVIDER,
        },
    }


@app.put("/api/config/character")
async def update_character(data: dict):
    """更新角色配置（名字、背景、性格、表达风格）"""
    profile = character_profiles.get("default", {})
    if not isinstance(profile, dict):
        profile = {}
    if "name" in data:
        profile["name"] = str(data["name"])[:20]
    if "background" in data:
        profile["background"] = str(data["background"])[:500]
    if "personality" in data:
        p = profile.setdefault("personality", {})
        pi = data["personality"]
        if "style" in pi:
            p["style"] = str(pi["style"])[:50]
        if "traits" in pi:
            t = pi["traits"]
            if isinstance(t, list):
                p["traits"] = [str(x)[:20] for x in t[:8]]
            elif isinstance(t, str):
                p["traits"] = [x.strip() for x in t.split("、")[:8]]
        if "max_sentences" in pi:
            try:
                p["max_sentences"] = max(1, min(3, int(pi["max_sentences"])))
            except (TypeError, ValueError):
                pass
    if "behaviors" in data:
        profile["behaviors"] = data["behaviors"]
    character_profiles["default"] = profile
    save_character()
    print(f"[CONFIG] 角色已更新: {profile.get('name', '小小倪')}", flush=True)
    return {"status": "ok", "character": {
        "name": profile.get("name", "小小倪"),
        "background": profile.get("background", ""),
        "personality": profile.get("personality", {}),
        "behaviors": profile.get("behaviors", {}),
    }}


@app.put("/api/config/memory")
async def update_memory_config(data: dict):
    """手动编辑长期记忆或清空对话历史"""
    mem = memory_store.setdefault("default", {"memory": "", "history": []})
    if "long_term" in data:
        mem["memory"] = str(data["long_term"])[:2000]
    if data.get("clear_history", False):
        mem["history"] = []
        print("[CONFIG] 对话历史已清空", flush=True)
    save_memory()
    return {
        "status": "ok",
        "memory": {
            "long_term": mem.get("memory", ""),
            "history_count": len(mem.get("history", [])) // 2,
        },
    }


@app.put("/api/config/runtime")
async def update_runtime(data: dict):
    """更新运行时参数（音量等）"""
    if "volume" in data:
        try:
            RUNTIME["volume"] = max(0.0, min(1.0, float(data["volume"])))
            print(f"[CONFIG] 音量已设为 {RUNTIME['volume']}", flush=True)
        except (TypeError, ValueError):
            pass
    return {"status": "ok", "runtime": dict(RUNTIME)}


# ============================================================
# Web 管理页面
# ============================================================
ADMIN_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>小小倪管理面板</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{--bg:#1a1a2e;--card:#16213e;--accent:#e94560;--text:#eee;--muted:#aaa;--green:#2ecc71;--blue:#3498db}
body{font-family:-apple-system,'Segoe UI',sans-serif;background:var(--bg);color:var(--text);padding:20px}
.container{max-width:960px;margin:0 auto}
h1{font-size:1.5em;margin-bottom:4px;display:flex;align-items:center;gap:10px}
h1 small{font-size:.5em;color:var(--muted);font-weight:400}
.subtitle{color:var(--muted);font-size:.85em;margin-bottom:20px}
.tabs{display:flex;gap:4px;margin-bottom:16px;flex-wrap:wrap}
.tab{padding:8px 20px;border-radius:8px 8px 0 0;cursor:pointer;background:#0f3460;font-size:.9em;transition:.2s}
.tab.active{background:var(--accent);color:#fff}
.tab:hover:not(.active){background:#1a4a7a}
.panel{display:none}
.panel.active{display:block}
.card{background:var(--card);border-radius:10px;padding:16px;margin-bottom:12px}
.card h3{color:#ffd700;font-size:.95em;margin-bottom:8px}
.chat-msg{margin:8px 0;padding:10px 14px;border-radius:8px;line-height:1.5;font-size:.9em}
.chat-user{background:#0f3460;margin-left:30px}
.chat-assistant{background:var(--card);border-left:3px solid var(--accent)}
.chat-role{font-size:.75em;color:var(--muted);margin-bottom:2px}
.rec-file{padding:10px 14px;margin:6px 0;background:#0f3460;border-radius:8px;display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.rec-file audio{flex:1;min-width:200px}
.rec-file .info{font-size:.82em;color:var(--muted)}
.stat-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:10px}
.stat-item{text-align:center;padding:16px;background:#0f3460;border-radius:8px}
.stat-item .num{font-size:1.8em;font-weight:bold;color:var(--accent)}
.stat-item .label{font-size:.78em;color:var(--muted);margin-top:4px}
.memory-box{background:#0d1117;padding:12px;border-radius:6px;font-size:.85em;color:#ccc;line-height:1.5;margin-top:8px;max-height:100px;overflow-y:auto}
.badge{display:inline-block;padding:2px 10px;border-radius:12px;font-size:.72em;font-weight:bold;margin-left:6px}
.badge-ok{background:var(--green);color:#000}
.badge-off{background:#555;color:#ccc}
.empty{text-align:center;padding:40px;color:var(--muted);font-size:.9em}
.loading{text-align:center;padding:20px;color:var(--muted)}
button{padding:6px 14px;border:none;border-radius:6px;cursor:pointer;font-size:.82em;background:var(--accent);color:#fff;transition:.2s}
input,textarea,select{background:#0f3460;border:1px solid #333;border-radius:6px;color:var(--text);padding:8px 10px;font-size:.85em;width:100%;box-sizing:border-box;font-family:inherit}
input:focus,textarea:focus,select:focus{outline:none;border-color:var(--accent)}
textarea{resize:vertical;min-height:60px}
label{display:block;font-size:.82em;color:var(--muted);margin-bottom:4px}
.form-group{margin-bottom:12px}
.form-row{display:flex;gap:12px;flex-wrap:wrap}
.form-row>*{flex:1;min-width:120px}
.tag-list{display:flex;flex-wrap:wrap;gap:6px;margin-top:4px}
.tag{border-radius:12px;padding:2px 10px;font-size:.78em;background:var(--accent);color:#fff}
.tag-add{display:flex;gap:6px;margin-top:4px}
.tag-add input{flex:1}
.tag-add button{padding:4px 12px;font-size:.78em}
.slider-group{display:flex;align-items:center;gap:10px}
.slider-group input[type=range]{flex:1;height:4px;-webkit-appearance:none;appearance:none;background:#333;border-radius:2px;outline:none;padding:0}
.slider-group input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;width:16px;height:16px;border-radius:50%;background:var(--accent);cursor:pointer}
.slider-group .value{min-width:36px;text-align:center;font-size:.9em;color:var(--accent);font-weight:bold}
.btn-small{padding:4px 10px;font-size:.76em}
.btn-danger{background:#e74c3c}
.btn-success{background:var(--green);color:#000}
.action-bar{display:flex;gap:8px;margin-top:8px;flex-wrap:wrap}
.toast{position:fixed;bottom:20px;right:20px;background:var(--green);color:#000;padding:10px 20px;border-radius:8px;font-size:.85em;z-index:999;opacity:0;transition:opacity .3s;pointer-events:none}
.toast.show{opacity:1}
.toast.err{background:#e74c3c;color:#fff}
button:hover{opacity:.8}
button.secondary{background:#0f3460;color:var(--text)}
.refresh{text-align:right;margin-bottom:8px}
</style>
</head>
<body>
<div class="container">
<h1>&#x1F9F8; 小小倪管理面板 <small>esp32_voice_assistant</small></h1>
<div class="subtitle" id="serverTime"></div>

<div class="tabs">
  <div class="tab active" onclick="switchTab(0)">&#x1F4AC; 对话记录</div>
  <div class="tab" onclick="switchTab(1)">&#x1F3A7; 远程拾音</div>
  <div class="tab" onclick="switchTab(2)">&#x2699;&#xFE0F; 服务器状态</div>
  <div class="tab" onclick="switchTab(3)">&#x1F527; 设备配置</div>
</div>

<!-- Tab 1: 对话记录 -->
<div class="panel active" id="tab0">
  <div class="refresh"><button class="secondary" onclick="loadHistory()">&#x1F503; 刷新</button></div>
  <div id="historyContent"><div class="loading">加载中...</div></div>
</div>

<!-- Tab 2: 远程拾音 -->
<div class="panel" id="tab1">
  <div class="refresh"><button class="secondary" onclick="loadRecordings()">&#x1F503; 刷新</button></div>
  <div id="recordingContent"><div class="loading">加载中...</div></div>
</div>

<!-- Tab 3: 服务器状态 -->
<div class="panel" id="tab2">
  <div class="refresh"><button class="secondary" onclick="loadStatus()">&#x1F503; 刷新</button></div>
  <div id="statusContent"><div class="loading">加载中...</div></div>
</div>

<!-- Tab 4: 设备配置 -->
<div class="panel" id="tab3">
  <div class="refresh"><button class="secondary" onclick="loadConfig()">&#x1F503; 刷新</button></div>
  <div id="configContent"><div class="loading">加载中...</div></div>
</div>

<div class="toast" id="toast"></div>
</div>

<script>
let currentTab = 0;
function switchTab(i){currentTab=i;document.querySelectorAll('.tab').forEach((t,idx)=>t.classList.toggle('active',idx===i));document.querySelectorAll('.panel').forEach((p,idx)=>p.classList.toggle('active',idx===i));if(i===0)loadHistory();if(i===1)loadRecordings();if(i===2)loadStatus();if(i===3)loadConfig()}

// 对话历史
async function loadHistory(){const el=document.getElementById('historyContent');el.innerHTML='<div class="loading">加载中...</div>';try{const r=await fetch('/api/history?limit=50');const d=await r.json();let html='';if(d.memory)html+='<div class="card"><h3>&#x1F9E0; 长期记忆</h3><div class="memory-box">'+d.memory+'</div></div>';if(d.conversations.length===0){html+='<div class="empty">暂无对话记录</div>'}else{const convs=[...d.conversations].reverse();for(const c of convs){html+='<div class="chat-msg chat-user"><div class="chat-role">&#x1F464; 用户</div>'+esc(c.user||'')+'</div>';html+='<div class="chat-msg chat-assistant"><div class="chat-role">&#x1F9F8; 小小倪</div>'+esc(c.assistant||'')+'</div>'}}html+='<div style="text-align:center;color:var(--muted);font-size:.8em;padding:8px">共 '+d.total+' 轮对话</div>';el.innerHTML=html}catch(e){el.innerHTML='<div class="empty">加载失败: '+e.message+'</div>'}}

// 远程拾音
async function loadRecordings(){const el=document.getElementById('recordingContent');el.innerHTML='<div class="loading">加载中...</div>';try{const r=await fetch('/listen/recordings');const d=await r.json();if(d.recordings.length===0){el.innerHTML='<div class="empty">暂无录音</div>';return}let html='';for(const f of d.recordings){html+='<div class="rec-file"><span class="info">'+f.file+' ('+f.duration_s+'s, '+Math.round(f.size/1024)+'KB)</span><audio controls preload="none"><source src="/audio/'+f.file+'" type="audio/wav"></audio></div>'}el.innerHTML=html}catch(e){el.innerHTML='<div class="empty">加载失败: '+e.message+'</div>'}}

// 服务器状态
async function loadStatus(){const el=document.getElementById('statusContent');el.innerHTML='<div class="loading">加载中...</div>';try{const r=await fetch('/api/status');const d=await r.json();const html='<div class="stat-grid">'
+'<div class="stat-item"><div class="num">'+d.clients+'</div><div class="label">已连接客户端</div></div>'
+'<div class="stat-item"><div class="num">'+d.conversation_count+'</div><div class="label">对话轮次</div></div>'
+'<div class="stat-item"><div class="num">'+d.recording_count+'</div><div class="label">录音文件</div></div>'
+'<div class="stat-item"><div class="num">'+(d.listening?'<span style=color:red>●</span>':'<span style=color:#555>●</span>')+'</div><div class="label">监听中</div></div>'
+'</div>'
+'<div class="card"><h3>快速操作</h3>'
+'<div style="display:flex;gap:8px;flex-wrap:wrap">'
+'<button onclick="startListen()">&#x1F534; 开启远程拾音</button>'
+'<button onclick="stopListen()">&#x2B1B; 停止远程拾音</button>'
+'<button onclick="refreshAll()">&#x1F503; 刷新全部</button>'
+'</div></div>';el.innerHTML=html}catch(e){el.innerHTML='<div class="empty">加载失败: '+e.message+'</div>'}}

async function startListen(){try{const r=await fetch('/listen/start',{method:'POST'});const d=await r.json();alert(d.status==='listening'?'监听已开启':'失败: '+d.message);loadStatus()}catch(e){alert('请求失败: '+e.message)}}
async function stopListen(){try{const r=await fetch('/listen/stop',{method:'POST'});const d=await r.json();alert(d.status==='stopped'?'监听已停止':d.message||'已停止');loadStatus()}catch(e){alert('请求失败: '+e.message)}}
function refreshAll(){loadHistory();loadRecordings();loadStatus()}
function esc(s){if(!s)return'';return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}

// ===== Toast 提示 =====
function showToast(msg,isErr){const t=document.getElementById('toast');t.textContent=msg;t.className='toast'+(isErr?' err':'')+' show';setTimeout(()=>t.classList.remove('show'),3000)}

// ===== 设备配置 =====
let configData={};

async function loadConfig(){const el=document.getElementById('configContent');el.innerHTML='<div class="loading">加载中...</div>';try{const r=await fetch('/api/config');if(!r.ok)throw new Error('HTTP '+r.status);configData=await r.json();renderConfig()}catch(e){el.innerHTML='<div class="empty">加载失败: '+e.message+'</div>'}}

function renderConfig(){const d=configData;if(!d)return;const c=d.character||{},p=c.personality||{},b=c.behaviors||{},r=d.runtime||{},m=d.memory||{},sys=d.system||{};
const traits=(p.traits||['温暖','自然']).join('、');
let html='';

// 1. 角色设定
html+='<div class="card"><h3>&#x1F9D9; 角色设定</h3>';
html+='<div class="form-row"><div class="form-group"><label>名字</label><input id="cfgName" value="'+esc(c.name||'小小倪')+'"></div>';
html+='<div class="form-group"><label>表达风格</label><select id="cfgStyle"><option value="口语中文"'+(p.style=='口语中文'?' selected':'')+'>口语中文</option><option value="温柔"'+(p.style=='温柔'?' selected':'')+'>温柔</option><option value="活泼"'+(p.style=='活泼'?' selected':'')+'>活泼</option><option value="幽默"'+(p.style=='幽默'?' selected':'')+'>幽默</option><option value="冷静"'+(p.style=='冷静'?' selected':'')+'>冷静</option><option value="知性"'+(p.style=='知性'?' selected':'')+'>知性</option></select></div>';
html+='<div class="form-group"><label>回复长度（最多几句话）</label><div class="slider-group"><input type="range" id="cfgMaxSentences" min="1" max="3" value="'+(p.max_sentences||2)+'" oninput="document.getElementById(\'sentVal\').textContent=this.value"><span class="value" id="sentVal">'+(p.max_sentences||2)+'</span></div></div></div>';
html+='<div class="form-group"><label>背景设定（AI扮演的角色描述）</label><textarea id="cfgBackground" rows="3">'+esc(c.background||'')+'</textarea></div>';
html+='<div class="form-group"><label>性格特质（用顿号分隔）</label><input id="cfgTraits" value="'+esc(traits)+'" placeholder="如：温暖、自然、偶尔调皮"></div>';
html+='<div class="action-bar"><button onclick="saveCharacter()" class="btn-success">&#x1F4BE; 保存角色</button></div></div>';

// 2. 记忆管理
html+='<div class="card"><h3>&#x1F9E0; 记忆管理</h3>';
html+='<div class="form-group"><label>长期记忆（AI能记住的关于你的信息）</label><textarea id="cfgMemory" rows="4">'+esc(m.long_term||'')+'</textarea></div>';
html+='<div style="font-size:.82em;color:var(--muted);margin-bottom:8px">当前对话轮次：<strong>'+(m.history_count||0)+'</strong></div>';
html+='<div class="action-bar"><button onclick="saveMemory()" class="btn-success">&#x1F4BE; 保存记忆</button><button onclick="clearHistory()" class="btn-danger">&#x1F5D1; 清空对话历史</button></div></div>';

// 3. 运行时设置
html+='<div class="card"><h3>&#x2699;&#xFE0F; 运行时设置</h3>';
html+='<div class="form-group"><label>音量</label><div class="slider-group"><input type="range" id="cfgVolume" min="0" max="100" value="'+(Math.round((r.volume||0.6)*100))+'" oninput="document.getElementById(\'volVal\').textContent=this.value+\'%\'"><span class="value" id="volVal">'+Math.round((r.volume||0.6)*100)+'%</span></div></div>';
html+='<div class="form-group"><label>TTS 引擎</label><input value="'+(sys.tts_provider||'aliyun')+'" disabled style="opacity:.6"></div>';
html+='<div class="action-bar"><button onclick="saveRuntime()" class="btn-success">&#x1F4BE; 保存设置</button></div></div>';

document.getElementById('configContent').innerHTML=html}

// 保存角色
async function saveCharacter(){const name=document.getElementById('cfgName').value.trim()||'小智';const bg=document.getElementById('cfgBackground').value.trim();const style=document.getElementById('cfgStyle').value;const traitsRaw=document.getElementById('cfgTraits').value;const maxSentences=parseInt(document.getElementById('cfgMaxSentences').value)||2;const traits=traitsRaw.split(/[、,，]/).map(t=>t.trim()).filter(Boolean);try{const r=await fetch('/api/config/character',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,background:bg,personality:{style,traits,max_sentences:maxSentences}})});if(!r.ok)throw new Error('HTTP '+r.status);const d=await r.json();showToast('角色已保存: '+d.character.name)}catch(e){showToast('保存失败: '+e.message,true)}}

// 保存记忆
async function saveMemory(){const longTerm=document.getElementById('cfgMemory').value.trim();try{const r=await fetch('/api/config/memory',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({long_term:longTerm})});if(!r.ok)throw new Error('HTTP '+r.status);showToast('记忆已保存')}catch(e){showToast('保存失败: '+e.message,true)}}

// 清空对话历史
async function clearHistory(){if(!confirm('确定要清空所有对话历史吗？此操作不可撤销。'))return;try{const r=await fetch('/api/config/memory',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({clear_history:true})});if(!r.ok)throw new Error('HTTP '+r.status);const d=await r.json();showToast('对话历史已清空（'+d.memory.history_count+'轮）');loadConfig()}catch(e){showToast('清空失败: '+e.message,true)}}

// 保存运行时
async function saveRuntime(){const volume=parseInt(document.getElementById('cfgVolume').value)/100;try{const r=await fetch('/api/config/runtime',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({volume})});if(!r.ok)throw new Error('HTTP '+r.status);showToast('设置已保存，音量: '+Math.round(volume*100)+'%')}catch(e){showToast('保存失败: '+e.message,true)}}

// 时钟
function updateTime(){document.getElementById('serverTime').textContent=new Date().toLocaleString('zh-CN')}
setInterval(updateTime,1000);updateTime();
loadHistory();
</script>
</body>
</html>"""


@app.get("/admin")
async def admin_page():
    return HTMLResponse(ADMIN_HTML)


# ============================================================
# WebSocket 端点（流式）
# ============================================================
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket, token: str = Query("")):
    # Token 验证
    if token != TOKEN:
        print(f"[WS] token 无效: {token}", flush=True)
        await ws.close(code=4001)
        return

    client_key = ws.client.host if ws.client else "unknown"

    # 打断：取消同一客户端的旧处理器
    prev = _active_handlers.get(client_key)
    if prev and not prev.done():
        print(f"[WS] 打断旧连接 {client_key}", flush=True)
        prev.cancel()

    current_task = asyncio.current_task()
    _active_handlers[client_key] = current_task

    print("[WS] 收到连接", flush=True)
    try:
        await ws.accept()
    except Exception as e:
        print(f"[WS] accept 失败: {e}", flush=True)
        return
    print("[WS] 已连接", flush=True)
    connected_clients[client_key] = ws
    client_last_activity[client_key] = time.monotonic()
    client_busy[client_key] = False

    audio_bytes_received = 0
    debug_audio = bytearray()

    try:
        while True:
            msg = await ws.receive()
            t = msg.get("type", "")

            if t == "websocket.disconnect":
                print("[WS] 断开", flush=True)
                break

            if t != "websocket.receive":
                print(f"[WS] 跳过 {t}", flush=True)
                continue

            # 二进制（PCM 块）
            if msg.get("bytes"):
                b = msg["bytes"]
                client_last_activity[client_key] = time.monotonic()

                # 远程监听模式：数据进入存档缓冲区，不触发对话流程
                if client_listen_mode.get(client_key, False):
                    client_listen_buffer.setdefault(client_key, bytearray()).extend(b)
                    continue

                client_busy[client_key] = True
                proactive = proactive_tasks.get(client_key)
                if proactive and not proactive.done():
                    proactive.cancel()
                audio_bytes_received += len(b)
                debug_audio.extend(b)
                continue

            # 文本
            txt = msg.get("text", "")
            if txt.startswith("CHAR:"):
                try:
                    profile = json.loads(txt[5:])
                    if not isinstance(profile, dict):
                        raise ValueError("角色档案必须是JSON对象")
                    character_profiles["default"] = profile
                    save_character()
                    print(f"[CHAR] 已加载角色: {profile.get('name', '未命名')}", flush=True)
                except (json.JSONDecodeError, ValueError) as e:
                    print(f"[CHAR] 角色档案无效: {e}", flush=True)
                continue
            if txt.startswith("SENSOR:"):
                try:
                    sensor = json.loads(txt[7:])
                    if isinstance(sensor, dict):
                        latest_sensors["default"] = sensor
                        print(f"[SENSOR] {sensor}", flush=True)
                except json.JSONDecodeError as error:
                    print(f"[SENSOR] 数据无效: {error}", flush=True)
                continue
            if txt == "LISTEN_DONE":
                buf = client_listen_buffer.pop(client_key, bytearray())
                client_listen_mode[client_key] = False
                client_busy[client_key] = False
                if buf:
                    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                    fname = f"listen_recordings/{ts}_{client_key}.wav"
                    os.makedirs("listen_recordings", exist_ok=True)
                    save_wav(bytes(buf), fname)
                    duration = len(buf) / 32000
                    print(
                        f"[LISTEN] ✅ 录音已存档: {fname} ({len(buf)} bytes, {duration:.1f}s)",
                        flush=True,
                    )
                continue
            if txt == "DONE":
                t0 = time.time()
                print(f"[WS] 音频收完 {audio_bytes_received} bytes PCM", flush=True)
                await asyncio.to_thread(save_input_debug_wav, bytes(debug_audio))

                # --- ASR (百度 REST 主力 → 阿里云备选) ---
                text = await transcribe_pcm(bytes(debug_audio))
                t_asr = time.time()
                if not text:
                    try:
                        async for chunk in tts_stream("我没听清"):
                            await ws.send_bytes(scale_pcm(chunk))
                        await ws.send_text("DONE")
                    except WebSocketDisconnect:
                        pass
                    audio_bytes_received = 0
                    debug_audio.clear()
                    client_busy[client_key] = False
                    client_last_activity[client_key] = time.monotonic()
                    continue

                local_file = route_fixed_command(text)
                if is_sleep_command(text):
                    await ws.send_text("SLEEP")
                    try:
                        await asyncio.wait_for(ws.receive(), timeout=1.5)
                    except (asyncio.TimeoutError, WebSocketDisconnect):
                        pass
                    await ws.send_text("LOCAL_DONE")
                    audio_bytes_received = 0
                    debug_audio.clear()
                    client_busy[client_key] = False
                    client_last_activity[client_key] = time.monotonic()
                    continue
                if local_file:
                    await ws.send_text("CMD:" + local_file)
                    try:
                        ack_message = await asyncio.wait_for(ws.receive(), timeout=1.5)
                        ack = ack_message.get("text", "")
                    except (asyncio.TimeoutError, WebSocketDisconnect):
                        ack = ""
                    if ack == "CMD_OK":
                        print(f"[CMD] 本地播放 {local_file}", flush=True)
                        await ws.send_text("LOCAL_DONE")
                        audio_bytes_received = 0
                        debug_audio.clear()
                        client_busy[client_key] = False
                        client_last_activity[client_key] = time.monotonic()
                        continue
                    print(f"[CMD] 本地文件不可用，回退云端: {local_file}", flush=True)

                # === 流式 LLM + 并行 TTS ===
                text_buffer = ""
                full_reply = ""
                is_first_sent = True
                tts_queue: asyncio.Queue = asyncio.Queue()
                t_llm_first = 0.0
                t_tts_first = 0.0

                async def llm_producer(cancel: asyncio.Event):
                    nonlocal full_reply, text_buffer, is_first_sent, t_llm_first
                    try:
                        async for delta in chat_stream(text, "default"):
                            if cancel.is_set():
                                break
                            full_reply += delta
                            if not t_llm_first:
                                t_llm_first = time.time()
                            text_buffer += delta
                            sentences, text_buffer = extract_complete_sentences(
                                text_buffer, aggressive=is_first_sent
                            )
                            for s in sentences:
                                if s.strip():
                                    await tts_queue.put(s.strip())
                                    if is_first_sent:
                                        is_first_sent = False
                        if text_buffer.strip() and not cancel.is_set():
                            await tts_queue.put(text_buffer.strip())
                    except (asyncio.CancelledError, WebSocketDisconnect):
                        cancel.set()
                    finally:
                        await tts_queue.put(None)

                async def tts_consumer(cancel: asyncio.Event) -> int:
                    nonlocal t_tts_first
                    sent = 0
                    pacer = PcmSendPacer()
                    try:
                        while True:
                            sentence = await tts_queue.get()
                            if sentence is None or cancel.is_set():
                                break
                            # 逐句 TTS → 直接播放，无预加载
                            async for chunk in tts_stream(sentence):
                                if cancel.is_set():
                                    break
                                if not t_tts_first:
                                    t_tts_first = time.time()
                                scaled = scale_pcm(chunk)
                                sent += await pacer.send(ws, scaled)
                    except (WebSocketDisconnect, OSError):
                        print("[TTS] WS断开，停止发送", flush=True)
                        cancel.set()
                    except asyncio.CancelledError:
                        cancel.set()
                    return sent

                cancel = asyncio.Event()
                try:
                    await asyncio.wait_for(
                        asyncio.gather(
                            llm_producer(cancel), tts_consumer(cancel),
                            return_exceptions=True
                        ),
                        timeout=25
                    )
                except asyncio.TimeoutError:
                    cancel.set()
                    print(f"[LLM] 对话超时(25s)，强制结束", flush=True)
                    if not t_tts_first:
                        # 完全没有 TTS 输出，发一句兜底
                        async for chunk in tts_stream("嗯，我没听清，再说一遍？"):
                            try:
                                await ws.send_bytes(scale_pcm(chunk))
                            except (WebSocketDisconnect, OSError):
                                break

                t_done = time.time()

                # --- 延迟报告 ---
                asr_ms = (t_asr - t0) * 1000
                llm_first_ms = (t_llm_first - t_asr) * 1000 if t_llm_first else 0
                tts_first_ms = (t_tts_first - (t_llm_first or t_asr)) * 1000 if t_tts_first else 0
                total_ms = (t_done - t0) * 1000
                speak_ms = (t_tts_first - t0) * 1000 if t_tts_first else 0
                print(
                    f"[TIME] ASR={asr_ms:.0f}ms | LLM首字={llm_first_ms:.0f}ms | "
                    f"TTS首音={tts_first_ms:.0f}ms | 松手到开口={speak_ms:.0f}ms | "
                    f"总计={total_ms:.0f}ms",
                    flush=True
                )

                asyncio.create_task(update_memory("default", text, full_reply, ws))

                try:
                    await ws.send_text("DONE")
                except WebSocketDisconnect:
                    pass
                audio_bytes_received = 0
                debug_audio.clear()
                client_busy[client_key] = False
                client_last_activity[client_key] = time.monotonic()

    except asyncio.CancelledError:
        print("[WS] 被新连接打断", flush=True)
    except WebSocketDisconnect:
        print("[WS] websocket 断开", flush=True)
    except Exception as e:
        print(f"[WS] 异常: {e}", flush=True)
    finally:
        if connected_clients.get(client_key) is ws:
            proactive = proactive_tasks.pop(client_key, None)
            if proactive and not proactive.done():
                proactive.cancel()
            connected_clients.pop(client_key, None)
            client_busy.pop(client_key, None)
            client_last_activity.pop(client_key, None)
            # 清理监听状态
            buffer = client_listen_buffer.pop(client_key, bytearray())
            if buffer and client_listen_mode.pop(client_key, False):
                ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                fname = f"listen_recordings/{ts}_{client_key}_disconnected.wav"
                os.makedirs("listen_recordings", exist_ok=True)
                save_wav(bytes(buffer), fname)
                duration = len(buffer) / 32000
                print(
                    f"[LISTEN] ⚠️ 断开时自动存档: {fname} ({len(buffer)} bytes, {duration:.1f}s)",
                    flush=True,
                )
        if _active_handlers.get(client_key) is current_task:
            del _active_handlers[client_key]


@app.get("/")
async def root():
    return {"status": "ok", "memory": memory_store.get("default", {}).get("memory", "")}


# ============================================================
# 启动
# ============================================================
if __name__ == "__main__":
    import uvicorn
    missing = []
    if not DEEPSEEK_API_KEY: missing.append("DEEPSEEK_API_KEY")
    if not BAIDU_APP_KEY or not BAIDU_SECRET_KEY: missing.append("BAIDU ASR credentials")
    if not DASHSCOPE_API_KEY: missing.append("DASHSCOPE_API_KEY (ASR/TTS)")
    if missing:
        print("⚠️  缺少配置:")
        for m in missing: print(f"    - {m}")
        print()
    print(f"服务端启动 http://{HOST}:{PORT}")
    print(f"  HTTP:  /chat           (兼容旧固件)")
    print(f"         /listen/start   (开启远程拾音)")
    print(f"         /listen/stop    (停止远程拾音)")
    print(f"         /listen/recordings (查看录音列表)")
    print(f"  WS:    /ws             (流式)")
    uvicorn.run(app, host=HOST, port=PORT)
