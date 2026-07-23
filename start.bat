@echo off
setlocal

cd /d "%~dp0"

if not exist "backend\.venv\Scripts\python.exe" (
  echo Initial setup is required.
  echo Run setup.bat first, then run start.bat.
  exit /b 1
)

if not exist "frontend\node_modules" (
  echo Frontend dependencies are missing.
  echo Run setup.bat first, then run start.bat.
  exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\dev.ps1"
exit /b %errorlevel%
