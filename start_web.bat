@echo off
cd /d "%~dp0"
set "PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
    echo Virtual environment not found. Follow the setup steps in README.md.
    exit /b 1
)
"%PYTHON%" -m flask --app app init-db
if errorlevel 1 exit /b 1
"%PYTHON%" -m flask --app app import-csv
if errorlevel 1 exit /b 1
"%PYTHON%" -m flask --app app run
