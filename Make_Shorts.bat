@echo off
chcp 65001 >nul
cd /d "%~dp0"
where ffmpeg >nul 2>nul || (echo Installing FFmpeg... & winget install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements & echo. & echo FFmpeg installed: CLOSE this window and run the file again.& pause & exit /b)
where python >nul 2>nul || (echo Installing Python... & winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements & echo. & echo Python installed: CLOSE this window and run the file again.& pause & exit /b)
python autocut.py "%~dp0Clips_to_edit" %*
pause
