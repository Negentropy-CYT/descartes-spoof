@echo off
setlocal
cd /d "%~dp0"
if not exist "%~dp0.venv\Scripts\python.exe" (
    echo Python environment missing: .venv
    exit /b 1
)
"%~dp0.venv\Scripts\python.exe" -B -u "%~dp0runner.py" %*
exit /b %ERRORLEVEL%
