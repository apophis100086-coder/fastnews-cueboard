@echo off
chcp 65001 >nul
cd /d "%~dp0"
"runtime\python\python.exe" -X utf8 "tools\stop_dashboard.py"
pause
