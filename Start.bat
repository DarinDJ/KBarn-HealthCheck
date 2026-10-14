@echo off
rem KBarn Health Check - double-click to start. First run installs what it needs (needs internet once).
cd /d "%~dp0"
title KBarn Health Check
if not exist ".venv\Scripts\python.exe" (
    echo First-time setup - this takes a few minutes, please wait...
    where python >nul 2>nul
    if errorlevel 1 (
        echo Python is not installed. Install Python 3.10 or newer from https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^) and run this again.
        pause
        exit /b 1
    )
    python -m venv .venv
    ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
    ".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt
    if errorlevel 1 (
        echo Setup failed - check your internet connection and run this again.
        pause
        exit /b 1
    )
)
echo Starting KBarn Health Check... your browser will open. Close this window to stop the program.
".venv\Scripts\python.exe" -m streamlit run app\streamlit_app.py --browser.gatherUsageStats false --server.headless false
pause
