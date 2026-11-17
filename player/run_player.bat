@echo off
rem Double-click to start the possession review player. See player\README.md.
cd /d "%~dp0.."

set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  echo Python was not found. Install Python 3 from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH" in the installer, then run this again.
  pause
  exit /b 1
)

%PY% player\serve.py %*
echo.
echo The player has stopped. If that was not on purpose, read the message above.
pause
