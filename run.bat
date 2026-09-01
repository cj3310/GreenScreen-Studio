@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUNBUFFERED=1
"C:\Users\Administrator\.workbuddy\binaries\python\envs\gs-studio\Scripts\python.exe" main.py
pause
