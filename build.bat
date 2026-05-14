@echo off
REM =========================================================
REM  NXAudioTool - Build Script
REM  The gate to (Music) Heaven
REM =========================================================

echo [NXAudio] Starting Build...
echo.

checking REM for PyInstaller
pyinstaller --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] PyInstaller not found. Please install:
    echo   pip install pyinstaller
    pause
    exit /b 1
)

REM Alten Build löschen
if exist dist\NXAudioTool rmdir /s /q dist\NXAudioTool
if exist build rmdir /s /q build

REM PyInstaller Build
pyinstaller ^
    --name "NXAudioTool" ^
    --windowed ^
    --icon "assets\icon.ico" ^
    --add-data "bin;bin" ^
    --add-data "assets;assets" ^
    --hidden-import "customtkinter" ^
    --hidden-import "tkinterdnd2" ^
    --hidden-import "pygame" ^
    --hidden-import "PIL" ^
    --collect-all "customtkinter" ^
    --collect-all "tkinterdnd2" ^
    --noconfirm ^
    src\main.py

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Build couldn't be finished!
    pause
    exit /b 1
)

echo.
echo [NXPlayer] Build successful!
echo Ausgabe: dist\NXAudioTool\NXAudioTool.exe
echo.
pause
