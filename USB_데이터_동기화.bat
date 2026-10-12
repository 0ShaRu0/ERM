@echo off
setlocal
cd /d "%~dp0"
python -X utf8 tools\install_independent.py --sync
if errorlevel 1 pause
endlocal
