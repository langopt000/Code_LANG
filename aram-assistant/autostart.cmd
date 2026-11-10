@echo off
rem Fallback logon entry (no script host required).
rem __ROOT__ / __PY__ are filled in at install time by setup_autostart.py,
rem so this file keeps working after the project is moved to another folder.
set "ARAM_ROOT=__ROOT__"
set "PY=__PY__"
if not exist "%PY%" set "PY=pythonw.exe"
cd /d "%ARAM_ROOT%"
start "" "%PY%" "watcher.py" >nul 2>&1
exit /b
