@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title FairPost - Local Web and MCP
python -m mcp_server.local_runtime web
if errorlevel 1 (
    echo Run python -m pip install -e ".[dev]" if dependencies are missing.
    pause
)
