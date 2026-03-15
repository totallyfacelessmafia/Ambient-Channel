@echo off
setlocal
title ChannelForge — First-Time Setup
color 0A

echo.
echo  ============================================
echo   ChannelForge — First-Time Setup
echo  ============================================
echo.

:: ── Check Python (try python first, then py launcher) ──
echo  [1/4] Checking Python...
set PYTHON=python
%PYTHON% --version >nul 2>&1
if errorlevel 1 (
    set PYTHON=py
    %PYTHON% --version >nul 2>&1
    if errorlevel 1 (
        echo.
        echo  ERROR: Python is not installed.
        echo  Download it from https://www.python.org/downloads/
        echo  Make sure to check "Add Python to PATH" during install.
        echo.
        pause
        exit /b 1
    )
)
for /f "tokens=*" %%i in ('%PYTHON% --version 2^>^&1') do echo         %%i
echo.

:: ── Check FFmpeg ──
echo  [2/4] Checking FFmpeg...
ffmpeg -version >nul 2>&1
if errorlevel 1 (
    echo.
    echo  ERROR: FFmpeg is not installed or not in PATH.
    echo  Download it from https://ffmpeg.org/download.html
    echo  Or install via: winget install FFmpeg
    echo.
    pause
    exit /b 1
)
for /f "tokens=1-3" %%a in ('ffmpeg -version 2^>^&1') do (
    echo         %%a %%b %%c
    goto :ffmpeg_done
)
:ffmpeg_done
echo.

:: ── Check GPU ──
echo  [3/4] Detecting GPU encoder...
ffmpeg -hide_banner -encoders 2>&1 | findstr /i "h264_nvenc" >nul 2>&1
if errorlevel 1 (
    echo         No NVIDIA GPU detected — will use CPU encoding (slower but works^)
) else (
    echo         NVIDIA NVENC detected — GPU encoding enabled
)
echo.

:: ── Install Python packages ──
echo  [4/4] Installing Python packages...
echo.
cd /d "%~dp0"
%PYTHON% -m pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo.
    echo  WARNING: Some packages may have failed to install.
    echo  Try running: %PYTHON% -m pip install -r requirements.txt
    echo.
)

echo.
echo  ============================================
echo   Setup complete! Run ChannelForge.bat to start.
echo  ============================================
echo.
pause
