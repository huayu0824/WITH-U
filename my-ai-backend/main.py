# main.py - FastAPI 主程序
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import Response, JSONResponse
from stt import speech_to_text
from llm import chat, clear_history
from tts import text_to_speech
import time

app = FastAPI(title="AI语音助手后端")

@app.get("/")
def root():
    return {"status": "running", "message": "AI语音助手后端正常运行"}

@app.post("/chat")
async def chat_endpoint(audio: UploadFile = File(...)):
    """
    ESP32发送音频 → 返回MP3语音回复
    接收：WAV音频文件（16kHz, 16bit, 单声道）
    返回：MP3音频
    """
    start_time = time.time()

    # 1. 读取音频
    audio_bytes = await audio.read()
    print(f"[1] 收到音频: {len(audio_bytes)} bytes")

    # 2. STT 语音识别
    user_text = speech_to_text(audio_bytes)
    print(f"[2] 识别结果: {user_text}")
    if not user_text:
        raise HTTPException(status_code=400, detail="语音识别失败，请重新说话")

    # 3. LLM 对话
    reply_text = chat(user_text)
    print(f"[3] AI回复: {reply_text}")

    # 4. TTS 语音合成
    audio_response = text_to_speech(reply_text)
    print(f"[4] 合成音频: {len(audio_response)} bytes")

    elapsed = time.time() - start_time
    print(f"[完成] 总耗时: {elapsed:.2f}秒")

    return Response(
        content=audio_response,
        media_type="audio/mpeg",
        headers={
            "Content-Length": str(len(audio_response)),
            "X-Elapsed": str(round(elapsed, 2))
        }
    )

@app.post("/chat/text")
async def chat_text_endpoint(text: str):
    """
    纯文字对话接口（测试用）
    """
    reply = chat(text)
    audio = text_to_speech(reply)
    return Response(content=audio, media_type="audio/mpeg")

@app.post("/clear")
def clear_endpoint():
    """清空对话历史"""
    clear_history()
    return {"status": "ok", "message": "对话历史已清空"}

@app.get("/tts/test")
async def tts_test(text: str = "你好，我是你的AI助手，系统运行正常。"):
    """测试TTS是否正常"""
    audio = text_to_speech(text)
    return Response(content=audio, media_type="audio/mpeg")
