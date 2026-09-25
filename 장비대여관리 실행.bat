@echo off
setlocal
cd /d "%~dp0"

if exist "%~dp0장비대여관리.exe" (
    start "" "%~dp0장비대여관리.exe"
    exit /b 0
)

set PY=
where python >nul 2>nul && set PY=python
if not defined PY where py >nul 2>nul && set PY=py

if not defined PY (
    echo [ERROR] Python was not found on this computer.
    echo Please install Python from: https://www.python.org/downloads/
    pause
    exit /b 1
)

"%PY%" -c "import flask, PIL, openpyxl, reportlab" >nul 2>nul
if errorlevel 1 (
    echo Installing required packages ...
    "%PY%" -m pip install --user -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Failed to install required packages.
        pause
        exit /b 1
    )
)

start "장비대여관리 서버" "%PY%" main.py
endlocal
