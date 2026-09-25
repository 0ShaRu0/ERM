@echo off
setlocal
cd /d "%~dp0"

if not defined ERM_PORT set ERM_PORT=8765

where adb >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Android Platform Tools ^(adb^) was not found.
    echo Install it and add adb to PATH, then run this file again.
    echo https://developer.android.com/tools/releases/platform-tools
    pause
    exit /b 1
)

adb get-state >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Android device was not found.
    echo 1. Enable Developer options and USB debugging on Android.
    echo 2. Connect the USB cable and approve this PC on the phone.
    pause
    exit /b 1
)

adb reverse tcp:%ERM_PORT% tcp:%ERM_PORT%
if errorlevel 1 (
    echo [ERROR] USB port connection failed.
    pause
    exit /b 1
)

adb shell am start -a android.intent.action.VIEW -d http://localhost:%ERM_PORT% >nul
echo [OK] Opened http://localhost:%ERM_PORT% on Android.
echo Keep this window or USB debugging connection active while synchronizing.
pause
endlocal
