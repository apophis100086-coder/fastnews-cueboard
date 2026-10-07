@echo off
cd /d "%~dp0"
"%~dp0runtime\python\python.exe" -B -X utf8 "%~dp0tools\restore_backup.py"
pause
