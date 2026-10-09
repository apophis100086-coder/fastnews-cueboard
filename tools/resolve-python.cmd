@echo off
set "FASTNEWS_PYTHON="
set "FASTNEWS_PYTHONW="
if exist "%~dp0..\runtime\python\python.exe" if exist "%~dp0..\runtime\python\pythonw.exe" (
  set "FASTNEWS_PYTHON=%~dp0..\runtime\python\python.exe"
  set "FASTNEWS_PYTHONW=%~dp0..\runtime\python\pythonw.exe"
  exit /b 0
)
if exist "%~dp0..\.venv\Scripts\python.exe" if exist "%~dp0..\.venv\Scripts\pythonw.exe" (
  set "FASTNEWS_PYTHON=%~dp0..\.venv\Scripts\python.exe"
  set "FASTNEWS_PYTHONW=%~dp0..\.venv\Scripts\pythonw.exe"
  exit /b 0
)
echo Project Python is missing. Run: python tools\setup_workspace.py
echo See README.md for source checkout setup, or extract the complete release package.
exit /b 1
