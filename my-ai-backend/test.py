# test.py - 本地测试，不需要ESP32
import httpx

BASE_URL = "http://localhost:8000"

def test_server():
    """测试服务器是否启动"""
    r = httpx.get(f"{BASE_URL}/")
    print("服务器状态:", r.json())

def test_tts():
    """测试TTS语音合成"""
    r = httpx.get(f"{BASE_URL}/tts/test", params={"text": "你好，测试成功！"})
    if r.status_code == 200:
        with open("test_output.mp3", "wb") as f:
            f.write(r.content)
        print("TTS测试成功！已保存到 test_output.mp3")
    else:
        print("TTS测试失败:", r.text)

def test_chat_text():
    """测试文字对话"""
    r = httpx.post(f"{BASE_URL}/chat/text", data={"text": "你好，介绍一下你自己"})
    if r.status_code == 200:
        with open("test_chat.mp3", "wb") as f:
            f.write(r.content)
        reply_text = r.headers.get("X-Reply-Text", "")
        print(f"对话测试成功！")
        print(f"AI回复: {reply_text}")
        print(f"音频已保存到 test_chat.mp3")
    else:
        print("对话测试失败:", r.text)

if __name__ == "__main__":
    print("=== 开始测试 ===")
    print("\n1. 测试服务器连接...")
    test_server()

    print("\n2. 测试TTS语音合成...")
    test_tts()

    print("\n3. 测试文字对话...")
    test_chat_text()

    print("\n=== 测试完成 ===")
