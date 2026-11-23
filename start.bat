@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

if not exist ".run" mkdir ".run"

REM ---- 0. Single-instance check ----
if exist ".run\pid" if exist ".run\port" (
    set /p OLDPID=<".run\pid"
    set /p OLDPORT=<".run\port"
    tasklist /FI "PID eq !OLDPID!" 2>nul | find "!OLDPID!" >nul
    if not errorlevel 1 (
        echo [novelista-v3] server already running on PID !OLDPID! (http://127.0.0.1:!OLDPORT!)
        start "" "http://127.0.0.1:!OLDPORT!"
        endlocal
        exit /b 0
    )
)

REM ---- 1. Find Python 3.11+ ----
set PY=
for %%C in ("py -3.14" "py -3.13" "py -3.12" "py -3.11" "py -3" "python") do (
    if not defined PY (
        %%~C -c "import sys;sys.exit(sys.version_info<(3,11))" >nul 2>&1
        if not errorlevel 1 set PY=%%~C
    )
)
if not defined PY (
    echo [novelista-v3] Python 3.11 or newer is required.
    pause
    exit /b 1
)

REM ---- 2. Virtual environment + dependencies ----
set VPY=backend\.venv\Scripts\python.exe
set MARK=backend\.venv\.installed
set NEED=0
if not exist "%VPY%" (
    echo [novelista-v3] creating virtual environment...
    !PY! -m venv backend\.venv
    if errorlevel 1 goto :setupfail
    set NEED=1
)
if not exist "%MARK%" set NEED=1
if "!NEED!"=="0" (
    for /f %%r in ('powershell -NoProfile -Command "if ((Get-Item 'backend\requirements.txt').LastWriteTime -gt (Get-Item '%MARK%').LastWriteTime) {1} else {0}"') do set NEED=%%r
)
if "!NEED!"=="1" (
    echo [novelista-v3] installing backend dependencies...
    "%VPY%" -m pip install -q --disable-pip-version-check -r backend\requirements.txt
    if errorlevel 1 goto :setupfail
    echo ok> "%MARK%"
)

REM ---- 3. Pick port 8000-8010 ----
set PORT=
for /l %%p in (8000,1,8010) do (
    if not defined PORT (
        netstat -ano | findstr /R /C:":%%p .*LISTENING" >nul
        if errorlevel 1 set PORT=%%p
    )
)
if not defined PORT (
    echo [novelista-v3] ports 8000-8010 are all in use.
    pause
    exit /b 1
)
echo [novelista-v3] starting on http://127.0.0.1:!PORT!

REM ---- 4. Rotate log & launch server ----
if exist ".run\server.log" move /Y ".run\server.log" ".run\server.prev.log" >nul 2>&1
del /q ".run\pid" ".run\port" 2>nul
echo !PORT!> ".run\port"
pushd backend
start "Novelista v3 Server" /min cmd /c ".venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port !PORT! > ..\.run\server.log 2>&1"
popd

set READY=0
for /l %%i in (1,1,30) do (
    if "!READY!"=="0" (
        set CODE=
        for /f %%c in ('curl -s -o nul -w "%%{http_code}" http://127.0.0.1:!PORT!/api/health 2^>nul') do set CODE=%%c
        if "!CODE!"=="200" (set READY=1) else timeout /t 1 /nobreak >nul
    )
)
if "!READY!"=="0" (
    echo [novelista-v3] server did not respond within 30s. Check .run\server.log
    pause
    exit /b 1
)

for /f "tokens=5" %%p in ('netstat -ano ^| findstr /R /C:":!PORT! .*LISTENING"') do if not "%%p"=="0" echo %%p> ".run\pid"
start "" "http://127.0.0.1:!PORT!"
endlocal
exit /b 0

:setupfail
echo [novelista-v3] setup failed.
pause
exit /b 1
