@echo off
setlocal
cd /d "%~dp0"
call ren-python.cmd
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8011 --workers 1 --no-access-log
