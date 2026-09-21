@echo off
REM Starts both halves in their own windows, detached from any parent shell.
REM Close the two windows to stop them.
start "newsroom api" cmd /k "cd /d %~dp0 && .venv\Scripts\python.exe -m uvicorn server.main:app --reload --host 127.0.0.1 --port 8000"
start "newsroom web" cmd /k "cd /d %~dp0 && npm run dev"
echo Backend  http://127.0.0.1:8000/api/health
echo Frontend http://localhost:3000
