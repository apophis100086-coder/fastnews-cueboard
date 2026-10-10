@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
call "%~dp0tools\resolve-python.cmd"
if errorlevel 1 (pause & exit /b 1)
"%FASTNEWS_PYTHON%" -B -X utf8 "%~dp0tools\restore_backup.py"
pause
