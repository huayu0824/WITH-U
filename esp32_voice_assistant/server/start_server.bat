@echo off
cd /d D:\玩偶\esp32_voice_assistant\server
python -m uvicorn server:app --host 0.0.0.0 --port 8000
