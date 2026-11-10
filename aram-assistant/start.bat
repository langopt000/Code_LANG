@echo off
chcp 65001 >nul
title ARAM Mayhem Assistant
cd /d "%~dp0"
set "PY=python"
where python >nul 2>nul || set "PY=py"
echo Starting ARAM assistant on http://127.0.0.1:8765/
echo Press Ctrl+C in this window to stop.
echo.
"%PY%" app.py
echo.
echo Server stopped. Press any key to close.
pause >nul
