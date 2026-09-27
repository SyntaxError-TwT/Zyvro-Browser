@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Project Python environment not found.
    echo Run: python -m venv .venv
    echo Then: .venv\Scripts\python -m pip install -r requirements.txt
    pause
    exit /b 1
)

".venv\Scripts\python.exe" "scratch_browser.py"
