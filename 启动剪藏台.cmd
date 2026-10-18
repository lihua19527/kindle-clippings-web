@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title Clippings Desk

where python >nul 2>nul
if errorlevel 1 (
  echo [x] Python not found on PATH. Install Python 3.9+ and try again.
  pause
  exit /b 2
)

echo Starting Clippings Desk ...
python -m app.server %*
set CODE=%errorlevel%

if not "%CODE%"=="0" (
  echo.
  echo [x] Exited with code %CODE%
  pause
)
endlocal
