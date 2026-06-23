import asyncio

from server import aliyun_tts_stream, transcribe_pcm


async def main():
    pcm_parts = [chunk async for chunk in aliyun_tts_stream("你好小智")]
    pcm = b"".join(pcm_parts)
    if not pcm:
        raise RuntimeError("TTS did not return audio")

    text = await transcribe_pcm(pcm)
    print(f"ASR_SELF_TEST text={text!r} pcm_bytes={len(pcm)}", flush=True)
    if not text:
        raise RuntimeError("ASR did not recognize the generated speech")


if __name__ == "__main__":
    asyncio.run(main())
