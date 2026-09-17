@echo off
cd /d "%~dp0"
echo Starting graphrag server on http://127.0.0.1:8000 ...
".venv\Scripts\graphrag.exe" serve --port 8000
pause
