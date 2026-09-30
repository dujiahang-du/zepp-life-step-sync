@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Create .venv and install requirements.txt first. See README.md.
  pause
  exit /b 1
)
echo Open http://127.0.0.1:5002 in your browser. Keep this window open.
".venv\Scripts\python.exe" -B run.py
pause
