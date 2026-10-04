@echo off
rem aside-shorts launcher.
rem   run.bat               open the side panel (server + docked window)
rem   run.bat make --job X  any CLI command: python -m aside_shorts ...
rem NOTE: .bat files must stay ASCII + CRLF (Korean cmd reads UTF-8/LF wrong).
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
if not exist ".venv\Scripts\python.exe" (
  echo Run setup.bat first.
  pause
  exit /b 1
)
if "%~1"=="" (
  title aside-shorts - KEEP THIS WINDOW OPEN while uploads are queued - minimize is OK
  echo.
  echo   aside-shorts is running. Keep this window open - minimize is OK.
  echo   Closing this window stops queued uploads - YouTube native schedules are not affected.
  echo   The side panel can be closed and reopened by running run.bat again.
  echo.
  ".venv\Scripts\python" -m aside_shorts ui
) else (
  ".venv\Scripts\python" -m aside_shorts %*
)
