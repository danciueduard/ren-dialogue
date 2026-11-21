@echo off
setlocal
cd /d "%~dp0"
call ren-python.cmd
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" -m app.ren_lan
