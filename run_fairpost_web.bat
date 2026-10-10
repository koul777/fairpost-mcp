@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title FairPost - Local Review
echo FairPost local review opens in your browser when the server is ready.
echo Stop this server with Ctrl+C.
python -m cli.main web --open-browser %*
if errorlevel 1 (
    echo Local startup failed. Check the message above.
    echo If packages are missing, run: python -m pip install .
    pause
    exit /b 1
)
