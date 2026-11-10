@echo off
chcp 65001 >nul
title Install ARAM Assistant autostart
cd /d "%~dp0"
set "PY=python"
where python >nul 2>nul || set "PY=py"
"%PY%" setup_autostart.py
echo.
pause
