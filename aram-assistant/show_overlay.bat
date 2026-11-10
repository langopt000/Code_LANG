@echo off
chcp 65001 >nul
title ARAM Assistant - Show Overlay
cd /d "%~dp0"
set "PY=python"
where python >nul 2>nul || set "PY=py"
"%PY%" show_overlay.py
echo.
pause
