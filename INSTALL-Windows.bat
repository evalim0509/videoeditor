@echo off
REM Double-click me to install autocut on this Windows computer.
cd /d "%~dp0"
echo Installing autocut - this takes a while the first time (downloads ~3 GB of tools and AI models).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup\windows.ps1"
echo.
pause
