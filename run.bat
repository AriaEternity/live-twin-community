@echo off
chcp 65001 >nul
setlocal
set FLASK_DEBUG=1
set PYTHONUTF8=1
.venv\Scripts\python.exe app.py
pause
