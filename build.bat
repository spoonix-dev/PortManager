@echo off
rem ============================================================================
rem  PortManager - one-click build  ->  dist\PortManager.exe
rem  Author: Mustafa Al-Aqrawi (@spoonix-dev)  |  MIT License
rem ============================================================================
setlocal
cd /d "%~dp0"
title PortManager build

where python >nul 2>nul
if errorlevel 1 (
    echo [!] Python 3.11+ was not found in PATH.
    goto :fail
)

rem Locally: build inside an isolated .venv. On CI (GitHub sets CI=true) use the runner's Python.
if /i not "%CI%"=="true" (
    if not exist ".venv\Scripts\python.exe" (
        echo [1/4] Creating virtual environment...
        python -m venv .venv || goto :fail
    )
    call ".venv\Scripts\activate.bat" || goto :fail
)

echo [2/4] Installing dependencies...
python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt || goto :fail

echo [3/4] Rendering the application icon (drawn in code, no asset files)...
set "ICON=%TEMP%\PortManager.ico"
python portmanager.py --export-icon "%ICON%" || goto :fail

echo [4/4] Compiling PortManager.exe with PyInstaller...
python -m PyInstaller --noconfirm --clean --onefile --noconsole ^
    --name PortManager ^
    --icon "%ICON%" ^
    --collect-all customtkinter ^
    --hidden-import pystray._win32 ^
    portmanager.py || goto :fail

echo.
echo ============================================================
echo  Done:  %CD%\dist\PortManager.exe
echo ============================================================
endlocal
exit /b 0

:fail
echo.
echo [X] Build failed.
endlocal
exit /b 1
