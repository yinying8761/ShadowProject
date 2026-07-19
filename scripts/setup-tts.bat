@echo off
setlocal enabledelayedexpansion
echo ========================================
echo   AI Companion - GPT-SoVITS TTS Setup
echo ========================================
echo.
echo This will install GPT-SoVITS for voice synthesis.
echo Required: Python 3.10, Git, ~6GB disk space, NVIDIA GPU (optional)
echo.

set "TTS_DIR=%~dp0\..\tools\GPT-SoVITS"
set "TTS_DIR=%TTS_DIR:\=/%"
for %%a in ("%TTS_DIR%") do set "TTS_DIR=%%~fa"
if not exist "%TTS_DIR%" mkdir "%TTS_DIR%"

echo [1/5] Cloning GPT-SoVITS...
cd /d "%TTS_DIR%"
if exist "%TTS_DIR%\GPT-SoVITS\.git" (
    echo Already cloned, skipping.
) else (
    git clone https://github.com/RVC-Boss/GPT-SoVITS.git
    if !ERRORLEVEL! neq 0 (
        echo ERROR: Git clone failed. Check your network or try a mirror:
        echo   git clone https://gitee.com/mirrors/GPT-SoVITS.git
        pause
        exit /b 1
    )
)

echo.
echo [2/5] Creating Python virtual environment...
cd /d "%TTS_DIR%\GPT-SoVITS"
where python3.10 >nul 2>&1
if !ERRORLEVEL! equ 0 (
    set "PYTHON=python3.10"
) else (
    set "PYTHON=python"
)
!PYTHON! --version 2>&1 | findstr "3.10 3.11" >nul
if !ERRORLEVEL! neq 0 (
    echo WARNING: Python 3.10 or 3.11 is recommended. Current version:
    !PYTHON! --version
)
if not exist "venv\Scripts\python.exe" (
    !PYTHON! -m venv venv
)

echo.
echo [3/5] Installing PyTorch (CUDA)...
call venv\Scripts\activate.bat
pip install torch==2.5.1 torchaudio==2.5.1 --index-url https://download.pytorch.org/whl/cu121 -i https://pypi.tuna.tsinghua.edu.cn/simple
if !ERRORLEVEL! neq 0 (
    echo WARNING: CUDA PyTorch failed, trying CPU version...
    pip install torch==2.5.1 torchaudio==2.5.1 -i https://pypi.tuna.tsinghua.edu.cn/simple
)

echo.
echo [4/5] Installing GPT-SoVITS dependencies...
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
if !ERRORLEVEL! neq 0 (
    echo WARNING: Some dependencies may have failed. GPT-SoVITS might still work in API mode.
)

echo.
echo [5/5] Downloading pretrained models...
echo This may take 10-30 minutes depending on your network.
python -c "from modelscope import snapshot_download; snapshot_download('lj1995/GPT-SoVITS', cache_dir='GPT_SoVITS/pretrained_models'); print('Models downloaded.')" 2>&1
if !ERRORLEVEL! neq 0 (
    echo.
    echo ============================================
    echo   Pretrained models download failed.
    echo   This is common due to network issues.
    echo.
    echo   Alternative: Download the integrated package:
    echo   1. Search "GPT-SoVITS 整合包" on B站
    echo   2. Download from the provided link
    echo   3. Extract and set TTS_REF_BASE in .env
    echo ============================================
)

echo.
echo ========================================
echo   GPT-SoVITS setup complete.
echo.
echo   Add this to your .env:
echo   TTS_REF_BASE=%TTS_DIR%\GPT-SoVITS
echo ========================================
pause
