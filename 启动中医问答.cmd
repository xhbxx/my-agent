@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo Python environment missing. See README.md to install dependencies.
    pause
    exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" -X utf8 -m app.launcher
