@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\start.ps1"
if errorlevel 1 (
    echo.
    echo Startup failed. Read the message above and try again.
    pause
    exit /b 1
)
