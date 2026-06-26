@echo off
REM ==========================================
REM NI DAQ Controller - Windows Launcher
REM ==========================================
title NI DAQ Controller
echo Starting NI DAQ Controller...
cd /d "%~dp0"
echo Python:
python --version
echo.
echo Dependencies: using requirements.txt
echo.
echo Launching web server on http://localhost:5000
echo Press Ctrl+C to stop
echo.
python start_web_server.py
pause
