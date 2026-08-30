@echo off
setlocal
chcp 65001 >nul

cd /d "%~dp0"
title FairPost MCP - Vercel Deploy

echo 현재 폴더의 FairPost MCP를 Vercel에 직접 배포합니다.
echo GitHub 저장소 복제 오류를 우회하는 방식입니다.
echo.

where npx >nul 2>&1
if errorlevel 1 (
    echo [오류] Node.js의 npx를 찾을 수 없습니다.
    echo Node.js를 설치한 뒤 다시 실행하세요.
    pause
    exit /b 1
)

echo Vercel 로그인 상태와 HRKIM 팀 권한이 필요합니다.
echo.
npx --yes vercel@57 deploy --prod --yes --project fairmcp --scope hrkim

echo.
echo 배포 명령이 끝났습니다.
pause
