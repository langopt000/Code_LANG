@echo off
rem Fallback logon entry (no script host required).
rem The project dir and python path below are filled in at install time by
rem setup_autostart.py, so this file keeps working after the project is moved.
set "ARAM_ROOT=__ROOT__"
set "PY=__PY__"
if not exist "%PY%" set "PY=pythonw.exe"
cd /d "%ARAM_ROOT%"
start "" "%PY%" "watcher.py" >nul 2>&1
exit /b
