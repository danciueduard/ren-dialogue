@echo off
if not exist ".venv\Scripts\python.exe" (
    py -3 -m venv .venv
    if errorlevel 1 exit /b 1
)
".venv\Scripts\python.exe" -c "import fastapi, httpx, uvicorn" >nul 2>&1
if not errorlevel 1 exit /b 0
".venv\Scripts\python.exe" -m pip install -r requirements.txt
exit /b %errorlevel%
