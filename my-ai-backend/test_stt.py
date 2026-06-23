# test_stt.py - 测试完整语音链路（用WAV文件）
import httpx
import urllib.parse

BASE_URL = "http://localhost:8000"

# 读取WAV文件
with open("D:/玩偶/my-ai-backend/test_input.wav", "rb") as f:
    wav_data = f.read()

print(f"WAV文件: {len(wav_data)} bytes")

# 发送到/chat端点（完整链路：STT → LLM → TTS）
r = httpx.post(
    f"{BASE_URL}/chat",
    files={"audio": ("audio.wav", wav_data, "audio/wav")}
)

print(f"状态码: {r.status_code}")

if r.status_code == 200:
    with open("D:/玩偶/my-ai-backend/chat_result.mp3", "wb") as f:
        f.write(r.content)

    user_text = urllib.parse.unquote(r.headers.get("X-User-Text", ""))
    reply_text = urllib.parse.unquote(r.headers.get("X-Reply-Text", ""))
    elapsed = r.headers.get("X-Elapsed", "")

    print(f"回复音频: {len(r.content)} bytes → chat_result.mp3")
    print(f"你说: {user_text}")
    print(f"AI: {reply_text}")
    print(f"耗时: {elapsed}秒")
else:
    print(f"错误: {r.text}")
