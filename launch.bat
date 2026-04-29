@echo off
setlocal
rem ============================================================
rem  Digitalinos Video Batch Pro - one-click Windows launcher
rem ------------------------------------------------------------
rem  Double-click this file. It will:
rem    1) create a local .venv (first run only)
rem    2) install Python dependencies (first run only)
rem    3) launch the app
rem  Requires: Python 3.9+ and FFmpeg on PATH.
rem ============================================================

pushd "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
    echo.
    echo [ERROR] Python is not on your PATH.
    echo Install Python 3.11 from https://www.python.org/downloads/windows/
    echo and tick "Add python.exe to PATH" during the installer.
    echo.
    pause
    exit /b 1
)

where ffmpeg >nul 2>&1
if errorlevel 1 (
    echo.
    echo [WARNING] FFmpeg is not on your PATH.
    echo The app will start but video processing will not work until you
    echo install FFmpeg. See README.md or run in PowerShell as admin:
    echo    winget install --id Gyan.FFmpeg --silent
    echo Then close this window and re-open launch.bat from a NEW terminal.
    echo.
    timeout /t 5 >nul
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment ^(first-time setup, ~30s^)...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
    call ".venv\Scripts\activate.bat"
    python -m pip install --upgrade pip
    echo Installing dependencies ^(first-time setup, ~60-90s^)...
    pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] pip install failed. Check your internet connection and try again.
        pause
        exit /b 1
    )
) else (
    call ".venv\Scripts\activate.bat"
)

echo.
echo Launching Digitalinos Video Batch Pro...
python run.py
set EXITCODE=%errorlevel%

popd
endlocal

if not "%EXITCODE%"=="0" (
    echo.
    echo [The app exited with error code %EXITCODE%]
    echo Press any key to close this window...
    pause >nul
)
