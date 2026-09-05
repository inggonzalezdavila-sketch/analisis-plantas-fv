@echo off
cd /d "%~dp0"
start "Análisis de Plantas FV" /b "C:\Users\USER\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" app.py
timeout /t 2 /nobreak >nul
start "" http://127.0.0.1:8000
