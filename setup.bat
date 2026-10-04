@echo off
rem aside-shorts setup - safe to run again.
rem NOTE: .bat files must stay ASCII + CRLF (Korean cmd reads UTF-8/LF wrong).
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONUTF8=1

echo [1/6] Python venv (.venv)
set "PYEXE=python"
where py >nul 2>nul && set "PYEXE=py -3"
if not exist ".venv\Scripts\python.exe" (
  %PYEXE% -m venv .venv
  if errorlevel 1 goto :fail
)
".venv\Scripts\python" -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
if errorlevel 1 (
  echo Python 3.11 or newer is required.
  goto :fail
)

echo [2/6] Python packages
".venv\Scripts\python" -m pip install -q --upgrade pip
".venv\Scripts\python" -m pip install -q -r requirements.txt
if errorlevel 1 goto :fail

echo [3/6] Playwright Chromium (motion rendering)
".venv\Scripts\python" -m playwright install chromium
if errorlevel 1 goto :fail

echo [4/6] Fonts and GSAP
".venv\Scripts\python" -m aside_shorts assets

echo [5/6] SuperTonic3 voice model (HuggingFace, about 380MB, first time only)
".venv\Scripts\python" -m aside_shorts tts-setup
if errorlevel 1 goto :fail

echo [6/6] Doctor (Claude, SuperTonic3, ffmpeg, Chrome)
where claude >nul 2>nul
if errorlevel 1 echo WARNING: claude CLI not found. Install: npm i -g @anthropic-ai/claude-code , then run: claude auth login
where ffmpeg >nul 2>nul
if errorlevel 1 echo WARNING: ffmpeg not found. Install: winget install Gyan.FFmpeg
".venv\Scripts\python" -m aside_shorts doctor

echo.
echo Setup finished.
echo Next: 1) claude auth login   (once, Claude subscription OAuth - scripts, images, motion)
echo       2) run.bat             (opens the side panel)
pause
exit /b 0

:fail
echo.
echo SETUP FAILED - read the messages above.
pause
exit /b 1
