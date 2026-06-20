@echo off
echo ========================================
echo   Installing AI Companion dependencies
echo ========================================
echo.
echo [1/2] Installing Python dependencies...
cd /d "%~dp0\..\backend"
pip install -r requirements.txt
if %ERRORLEVEL% neq 0 (
    echo ERROR: Python dependencies installation failed.
    pause
    exit /b 1
)

echo.
echo [2/2] Installing Node.js dependencies...
cd /d "%~dp0\..\frontend"
call npm install
if %ERRORLEVEL% neq 0 (
    echo ERROR: Node.js dependencies installation failed.
    pause
    exit /b 1
)

echo.
echo ========================================
echo   Installation complete!
echo ========================================
pause
