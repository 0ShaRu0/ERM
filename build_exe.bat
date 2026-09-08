@echo off
setlocal
cd /d "%~dp0"

set PY=
where py >nul 2>nul && set PY=py
if not defined PY where python >nul 2>nul && set PY=python

if not defined PY (
    echo [ERROR] Python is required only to build the executable.
    pause
    exit /b 1
)

"%PY%" -m pip install --user -r requirements.txt
if errorlevel 1 goto :build_failed

"%PY%" -c "import PyInstaller" >nul 2>nul
if errorlevel 1 (
    "%PY%" -m pip install --user "pyinstaller>=6.15"
    if errorlevel 1 goto :build_failed
)

if exist build_exe_temp rmdir /s /q build_exe_temp
if exist "장비대여관리.spec" del /q "장비대여관리.spec"

"%PY%" -m PyInstaller --noconfirm --clean --onefile --windowed --name "장비대여관리" --distpath "." --workpath "build_exe_temp" main.py
if errorlevel 1 goto :build_failed

if exist build_exe_temp rmdir /s /q build_exe_temp
if exist "장비대여관리.spec" del /q "장비대여관리.spec"

echo.
echo [OK] 장비대여관리.exe was created.
pause
exit /b 0

:build_failed
echo.
echo [ERROR] Failed to build 장비대여관리.exe.
pause
exit /b 1
