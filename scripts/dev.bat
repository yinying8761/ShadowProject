@echo off
echo ========================================
echo   Starting AI Companion (Dev Mode)
echo ========================================
echo.

set ROOT=%~dp0..

echo [1/3] Starting Python backend...
start "AI-Companion-Backend" cmd /k "cd /d "%ROOT%\backend" && python main.py"

echo [2/3] Waiting 2s for backend to come up...
timeout /t 2 /nobreak >nul

echo [3/3] Starting Electron desktop window (vite + electron)...
start "AI-Companion-Frontend" cmd /k "cd /d "%ROOT%\frontend" && npm run electron:dev"

echo.
echo Backend:  http://localhost:8722
echo Frontend: http://localhost:5173  (dev server)
echo Desktop window will appear after Vite is ready.
echo.
echo To stop: close both windows.
pause
