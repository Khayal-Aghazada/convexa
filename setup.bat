@echo off
rem One-time setup: creates a local Python environment, installs libraries, starts Convexa.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    python -m venv .venv || (echo Python 3.10+ is required: https://www.python.org/downloads/ & pause & exit /b 1)
)
echo Installing libraries...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt || (pause & exit /b 1)
echo Starting Convexa...
start "" ".venv\Scripts\pythonw.exe" "%~dp0convexa.pyw"
echo Done. Convexa is in your system tray. Press Ctrl+Alt+C in Explorer to convert selected files.
timeout /t 4 >nul
