# stt.py - 阿里云语音识别（HTTP REST版，无需额外SDK）
import requests
from config import ALIYUN_APP_KEY
from aliyun_token import get_token

def speech_to_text(audio_bytes: bytes, sample_rate: int = 16000) -> str:
    """
    输入：PCM音频字节（16kHz, 16bit, 单声道）
    输出：识别出的文字
    """
    url = "https://nls-gateway.cn-shanghai.aliyuncs.com/stream/v1/asr"

    # 生成Token
    token = get_token()
    if not token:
        return ""

    headers = {
        "X-NLS-Token": token,
        "Content-Type": "application/octet-stream",
        "Accept": "application/json",
    }

    params = {
        "appkey": ALIYUN_APP_KEY,
        "format": "pcm",
        "sample_rate": sample_rate,
        "enable_punctuation_prediction": "true",
        "enable_inverse_text_normalization": "true",
    }

    response = requests.post(
        url,
        headers=headers,
        params=params,
        data=audio_bytes,
        timeout=10
    )

    result = response.json()
    print(f"[STT] 返回: {result}")

    if result.get("status") == 20000000:
        return result.get("result", "")
    else:
        print(f"[STT] 识别失败: {result.get('message', '')}")
        return ""

