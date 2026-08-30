@echo off
setlocal
chcp 65001 >nul

set "PROJECT_DIR=%~dp0"
if not exist "%PROJECT_DIR%pyproject.toml" if exist "C:\workspace\fairmcp\pyproject.toml" set "PROJECT_DIR=C:\workspace\fairmcp\"
cd /d "%PROJECT_DIR%"
title FairPost MCP - Local Server

where fairpost-mcp >nul 2>&1
if errorlevel 1 (
    echo.
    echo [오류] fairpost-mcp 명령을 찾을 수 없습니다.
    echo 먼저 프로젝트 폴더에서 다음 명령을 실행하세요:
    echo.
    echo     python -m pip install -e ".[dev]"
    echo.
    pause
    exit /b 1
)

echo FairPost MCP를 시작합니다.
echo 종료하려면 이 창에서 Ctrl+C를 누르세요.
echo.
echo MCP 주소: http://127.0.0.1:8000/mcp
echo.

fairpost-mcp

echo.
echo FairPost MCP가 종료되었습니다.
pause
