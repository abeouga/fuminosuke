@echo off
setlocal

cd /d "%~dp0"

if not exist "backend\.venv\Scripts\python.exe" goto :setup
if not exist "backend\.venv\Scripts\alembic.exe" goto :setup
if not exist "frontend\node_modules" goto :setup
if not exist "backend\.env" goto :missing_env
"backend\.venv\Scripts\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)" >nul 2>&1
if errorlevel 1 goto :setup
goto :start

:missing_env
echo Startup stopped because backend\.env is missing.
echo Copy backend\.env.example to backend\.env, review OCR_PROVIDER,
echo and set OPENAI_API_KEY when using external OCR. Then run start.bat again.
exit /b 1

:setup
echo Preparing the application for its first start...
call "%~dp0setup.bat"
if errorlevel 1 exit /b %errorlevel%

:start
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\dev.ps1"
exit /b %errorlevel%
