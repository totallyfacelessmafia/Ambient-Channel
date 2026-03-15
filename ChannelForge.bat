@echo off
setlocal
title ChannelForge
color 0F
cd /d "%~dp0"

:: ── Detect Python command (python or py) ──
set PYTHON=python
%PYTHON% --version >nul 2>&1
if errorlevel 1 (
    set PYTHON=py
    %PYTHON% --version >nul 2>&1
    if errorlevel 1 (
        echo  ERROR: Python not found. Install from https://www.python.org/downloads/
        pause
        exit /b 1
    )
)

:: ── First-run check: if requirements aren't installed, run setup ──
%PYTHON% -c "import flask" >nul 2>&1
if errorlevel 1 (
    echo  First run detected — running setup...
    echo.
    call setup.bat
    if errorlevel 1 exit /b 1
)

:: ── Auto-update from GitHub (if .git exists) ──
if exist ".git" (
    echo  Checking for updates...
    git pull --ff-only >nul 2>&1
    if not errorlevel 1 (
        echo  Updated to latest version.
    ) else (
        echo  Skipping update (no changes or not connected^).
    )
    echo.
)

:: ── Kill any existing server on port 5000 ──
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":5000.*LISTENING" 2^>nul') do (
    taskkill /PID %%a /F >nul 2>&1
)

:: ── Start the server ──
echo.
echo  ============================================
echo   ChannelForge is starting...
echo  ============================================
echo.
echo  Dashboard:  http://localhost:5000
echo  Press Ctrl+C to stop the server.
echo.

:: Open browser after a short delay
start "" cmd /c "timeout /t 3 /nobreak >nul && start http://localhost:5000"

:: Launch Flask
cd dashboard
%PYTHON% app.py

echo.
echo  Server stopped.
pause
