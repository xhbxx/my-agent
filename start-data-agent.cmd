@echo off
setlocal

"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0examples\safe_analysis\start.ps1" %*
set "launch_exit=%errorlevel%"
if not "%launch_exit%"=="0" (
    echo.
    echo DATA-AGENT exited with code %launch_exit%. See the message above.
    pause
)
exit /b %launch_exit%
