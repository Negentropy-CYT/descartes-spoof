@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0.venv\Scripts\pythonw.exe" (
    start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0spoof_gui.py"
) else (
    echo Missing Python environment. To create the standalone Spoof.exe, run build_desktop.ps1 on Windows.
    pause
    exit /b 1
)
