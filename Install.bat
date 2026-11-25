@echo off
chcp 65001 >nul
cd /d "%~dp0"
where ffmpeg >nul 2>nul || (echo Installing FFmpeg... & winget install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements & echo. & echo FFmpeg installed: CLOSE this window and run the file again.& pause & exit /b)
where python >nul 2>nul || (echo Installing Python... & winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements & echo. & echo Python installed: CLOSE this window and run the file again.& pause & exit /b)
where deno >nul 2>nul || (echo Installing Deno... & winget install -e --id DenoLand.Deno --accept-source-agreements --accept-package-agreements & echo. & echo Deno installed: CLOSE this window and run the file again.& pause & exit /b)
python -m pip install -q -U "yt-dlp[default]" requests
powershell -NoProfile -Command "$s=(New-Object -COM WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\Shorts Bot.lnk'); $s.TargetPath=(Get-Command pythonw).Source; $s.Arguments='%~dp0ShortsApp.pyw'; $s.WorkingDirectory='%~dp0'; $s.Save()"
python "%~dp0ShortsApp.pyw" --set-startup on
echo.
echo Done! You will find the "Shorts Bot" icon on your Desktop, and it starts with Windows. Opening the app...
start "" pythonw "%~dp0ShortsApp.pyw"
timeout /t 4 >nul
