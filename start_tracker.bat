@echo off
REM Last.fm Tracker - Windows Startup Script
REM Double-click this file to start the tracker

cd /d "%~dp0"

REM Check if virtual environment exists
if not exist "venv\Scripts\activate.bat" (
    echo Virtual environment not found. Creating...
    python -m venv venv
    call venv\Scripts\activate.bat
    pip install -r requirements.txt
) else (
    call venv\Scripts\activate.bat
)

REM Start the service
echo Starting Last.fm Tracker...
echo Dashboard available at: http://localhost:5000
echo Press Ctrl+C to stop
python run_service.py
