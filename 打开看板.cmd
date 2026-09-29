@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "runtime\python\pythonw.exe" (echo 未找到随包运行时，请先完整解压试用包。& pause & exit /b 1)
start "" "%~dp0runtime\python\pythonw.exe" "%~dp0tools\open_dashboard.py"
