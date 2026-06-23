# tts.py - 阿里云 CosyVoice 超拟声语音合成（DashScope API）
# 文档: https://help.aliyun.com/zh/model-studio/cosyvoice-python-sdk
import dashscope
from dashscope.audio.tts_v2 import SpeechSynthesizer
from config import DASHSCOPE_API_KEY

dashscope.api_key = DASHSCOPE_API_KEY

# CosyVoice 标准音色（克隆你自己的之前先用这个测试）
# 可选: longxiaochun(女), shanshan(女), tiechui(男), etc.
DEFAULT_VOICE = "cosyvoice-v3.5-plus-myvoice2-842dc28ec8ec4e288b0a8177e18517fd"

MODEL = "cosyvoice-v3.5-plus"

# 缓存合成器（避免每次重新初始化 WebSocket 连接）
_synthesizer = None

def _get_synthesizer(voice: str = None):
    global _synthesizer
    if _synthesizer is None:
        _synthesizer = SpeechSynthesizer(
            model=MODEL,
            voice=voice or DEFAULT_VOICE,
        )
    return _synthesizer

def text_to_speech(text: str, voice: str = None) -> bytes:
    """
    输入：文字
    输出：MP3音频字节
    voice: 指定音色（默认用标准音色，克隆后传 voice_id）
    """
    if not DASHSCOPE_API_KEY or DASHSCOPE_API_KEY.startswith("sk-你的"):
        raise Exception("DashScope API Key 未配置，请在 config.py 中填写")

    try:
        synth = _get_synthesizer(voice)
        audio = synth.call(text)
        return audio
    except Exception as e:
        # 如果连接断了，重置合成器重试一次
        global _synthesizer
        _synthesizer = None
        synth = _get_synthesizer(voice)
        return synth.call(text)


# ===== 声音克隆辅助函数（后续使用） =====
# from dashscope.audio.tts_v2 import VoiceEnrollmentService
#
# def create_cloned_voice(audio_url: str, prefix: str = "myvoice") -> str:
#     """
#     克隆音色
#     audio_url: 录音文件的公网可访问URL（建议10秒左右干净人声）
#     prefix: 音色名前缀（<=10字符，仅字母数字）
#     返回: voice_id（传给 text_to_speech 的 voice 参数即可使用）
#     """
#     service = VoiceEnrollmentService(api_key=DASHSCOPE_API_KEY)
#     voice_id = service.create_voice(
#         target_model=MODEL,
#         prefix=prefix,
#         url=audio_url,
#         language_hints=["zh"],
#         enable_preprocess=True,
#     )
#     return voice_id
