@echo off
rem Sets up the desk presence PC helper. Double-click it once: it installs pyserial
rem if needed, adds the helper to your Startup apps (so it runs without a window
rem each time you log in) and starts it. Double-click it again after an update.
rem It finds Python: the py launcher from python.org, Anaconda, or python.
title Desk presence helper
cd /d "%~dp0"
set "PY="
rem The py launcher can outlive the Python it came with, so check it starts one.
py -c "import sys" >nul 2>nul && set "PY=py"
if not defined PY if exist "%USERPROFILE%\anaconda3\python.exe" set "PY=%USERPROFILE%\anaconda3\python.exe"
if not defined PY if exist "%ProgramData%\anaconda3\python.exe" set "PY=%ProgramData%\anaconda3\python.exe"
if not defined PY set "PY=python"
"%PY%" -c "import serial" 2>nul || "%PY%" -m pip install pyserial
rem If it can't start in the background, it runs in this window to show why.
"%PY%" desk_helper.py --install || "%PY%" desk_helper.py
pause
