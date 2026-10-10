@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
call "%~dp0tools\resolve-python.cmd"
if errorlevel 1 (pause & exit /b 1)
start "" "%FASTNEWS_PYTHONW%" "%~dp0tools\open_dashboard.py" --entry
