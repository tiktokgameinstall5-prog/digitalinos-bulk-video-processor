@echo off
setlocal enabledelayedexpansion
rem ============================================================
rem  Digitalinos Video Batch Pro - one-click Windows .exe build
rem ------------------------------------------------------------
rem  This script:
rem    1) creates / updates a build virtualenv (.venv-build)
rem    2) installs Nuitka + build deps
rem    3) compiles the Python code to native machine code with
rem       Nuitka in --onefile mode and packages a single .exe
rem
rem  Output:  dist\Digitalinos.exe
rem  Requires: Python 3.11+ on PATH and a C compiler (Nuitka will
rem            offer to download MinGW64 on first run).
rem ============================================================

pushd "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not on your PATH. Install Python 3.11+ first.
    pause
    exit /b 1
)

if not exist ".venv-build\Scripts\python.exe" (
    echo Creating build virtualenv ^(first-time setup, ~30s^)...
    python -m venv .venv-build
    if errorlevel 1 ( echo [ERROR] venv create failed.& pause & exit /b 1 )
)

call ".venv-build\Scripts\activate.bat"
python -m pip install --upgrade pip >nul
pip install -r requirements-build.txt
if errorlevel 1 ( echo [ERROR] pip install failed.& pause & exit /b 1 )

if not exist "dist" mkdir dist

echo.
echo === Compiling Digitalinos to native binary with Nuitka (5-15 min on first run) ===
python -m nuitka ^
    --onefile ^
    --assume-yes-for-downloads ^
    --windows-console-mode=disable ^
    --enable-plugin=pyqt5 ^
    --include-package=app ^
    --include-package=app.licensing ^
    --include-package=app.processing ^
    --include-package=app.ui ^
    --include-package=app.utils ^
    --output-dir=dist ^
    --output-filename=Digitalinos.exe ^
    --company-name="Digitalinos" ^
    --product-name="Digitalinos Video Batch Pro" ^
    --file-version=0.2.0.0 ^
    --product-version=0.2.0.0 ^
    run.py
if errorlevel 1 ( echo [ERROR] Nuitka build failed.& exit /b 1 )

if exist "dist\Digitalinos.exe" (
    echo.
    echo Build OK: dist\Digitalinos.exe
) else (
    echo [ERROR] dist\Digitalinos.exe not found.
    exit /b 1
)

popd
endlocal
