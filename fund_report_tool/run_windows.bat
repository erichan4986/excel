@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
    echo Virtual environment not found at .venv\Scripts\activate.bat
    echo Please create one with: python -m venv .venv
    echo Then install dependencies with: .venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)

call .venv\Scripts\activate.bat
uvicorn app:app --host 127.0.0.1 --port 8000 --reload
