@echo off
rem ============================================================================
rem  PortManager 2.4 - one-click build  ->  dist\PortManager.exe
rem  Author: Mustafa Al-Aqrawi (@spoonix-dev)  |  MIT License
rem
rem  The locales\ folder is bundled into the exe (--add-data). Users can still add
rem  languages without rebuilding by putting extra .json files in a "locales"
rem  folder next to PortManager.exe.
rem ============================================================================
setlocal
cd /d "%~dp0"
title PortManager build

where python >nul 2>nul
if errorlevel 1 (
    echo [!] Python 3.11+ was not found in PATH.
    goto :fail
)

if not exist "locales\en.json" (
    echo [!] locales\en.json is missing - the locales folder must sit next to build.bat.
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

echo [4/4] Compiling PortManager.exe with PyInstaller (bundling locales\)...
python -m PyInstaller --noconfirm --clean --onefile --noconsole ^
    --name PortManager ^
    --icon "%ICON%" ^
    --add-data "locales;locales" ^
    --collect-all customtkinter ^
    --hidden-import pystray._win32 ^
    portmanager.py || goto :fail

echo.
echo ============================================================
echo  Done:  %CD%\dist\PortManager.exe
echo  Tip :  to add a language, create a "locales" folder next to
echo         the exe and drop a .json file in it (see README).
echo ============================================================
endlocal
exit /b 0

:fail
echo.
echo [X] Build failed.
endlocal
exit /b 1
