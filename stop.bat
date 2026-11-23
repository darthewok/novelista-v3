@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

if exist ".run\pid" (
    set /p SPID=<".run\pid"
    if defined SPID (
        echo [novelista-v3] stopping server PID !SPID!...
        taskkill /PID !SPID! /T /F >nul 2>&1
    )
    del /q ".run\pid" ".run\port" 2>nul
) else (
    echo [novelista-v3] no tracked server PID found.
)

if "%1"=="--full" (
    echo [novelista-v3] stopping Ollama if running...
    taskkill /IM ollama.exe /F >nul 2>&1
)
endlocal
exit /b 0
