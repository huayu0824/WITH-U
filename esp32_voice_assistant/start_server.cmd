@echo off
cd /d "%~dp0server"
start "ESP32 Voice Server" /b "%~dp0.venv\Scripts\python.exe" -u server.py 1>>server_live.log 2>>server_error.log
