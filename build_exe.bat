@echo off
rem Builds dist\KBarnHealthCheck\KBarnHealthCheck.exe - a folder that runs on any Windows PC WITHOUT Python.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)
".venv\Scripts\python.exe" -m pip install --quiet pyinstaller pywebview
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --noconsole --onedir ^
  --name KBarnHealthCheck --icon assets\icon.ico ^
  --add-data "app;app" --add-data "kbarn_health;kbarn_health" ^
  --collect-all streamlit --collect-all plotly --copy-metadata streamlit --copy-metadata plotly ^
  --copy-metadata pandas --copy-metadata numpy --copy-metadata pyyaml --copy-metadata pywebview ^
  --hidden-import pandas --hidden-import numpy --hidden-import yaml --hidden-import tkinter ^
  --hidden-import webview --hidden-import webview.platforms.edgechromium --hidden-import webview.platforms.winforms ^
  --exclude-module matplotlib --exclude-module scipy --exclude-module IPython --exclude-module pytest --exclude-module playwright ^
  launcher.py
echo.
echo Done. Copy the whole folder dist\KBarnHealthCheck to the other PC and run KBarnHealthCheck.exe
pause
