@echo off
echo ========================================
echo   AI Companion - Install Dependencies
echo ========================================
echo.

echo [1/2] Installing Python dependencies...
cd /d "%~dp0\..\backend"
pip install -r requirements.txt
if %ERRORLEVEL% neq 0 (
    echo ERROR: Python dependencies failed. Make sure Python 3.10+ is installed.
    pause
    exit /b 1
)

echo.
echo [2/2] Installing Node.js dependencies...
cd /d "%~dp0\..\frontend"
call npm install
if %ERRORLEVEL% neq 0 (
    echo ERROR: Node.js dependencies failed. Make sure Node.js 18+ is installed.
    pause
    exit /b 1
)

echo.
echo ========================================
echo   Installation complete.
echo.
echo   Next steps:
echo   1. Copy .env.example to .env and edit your API keys
echo   2. (Optional) Run setup-tts.bat for voice synthesis
echo   3. Run dev.bat to start
echo ========================================
pause
