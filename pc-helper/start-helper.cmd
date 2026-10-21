@echo off
rem Runs the desk presence PC helper in this window, so you can see what it does.
rem Double-click it. It finds Python (the py launcher from python.org, Anaconda,
rem or python) and installs pyserial the first time.
cd /d "%~dp0"
set "PY="
where py >/dev/null 2>/dev/null && set "PY=py"
if not defined PY if exist "%USERPROFILE%\anaconda3\python.exe" set "PY=%USERPROFILE%\anaconda3\python.exe"
if not defined PY if exist "%ProgramData%\anaconda3\python.exe" set "PY=%ProgramData%\anaconda3\python.exe"
if not defined PY set "PY=python"
"%PY%" -c "import serial" 2>/dev/null || "%PY%" -m pip install pyserial
"%PY%" desk_helper.py
pause
