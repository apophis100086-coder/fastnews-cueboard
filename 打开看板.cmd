@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "FASTNEWS_PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%FASTNEWS_PYTHON%" set "FASTNEWS_PYTHON=%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\python.exe"
if not exist "%FASTNEWS_PYTHON%" set "FASTNEWS_PYTHON=python"
"%FASTNEWS_PYTHON%" "tools\open_dashboard.py" %*
if errorlevel 1 pause
