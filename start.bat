@echo off
setlocal

cd /d "%~dp0"

rem Settings supplied by the parent Windows session override backend\.env.
rem Clear all app-setting overrides in this child process so the pulled
rem backend\.env, including its DATA_DIR and DATABASE_URL, takes precedence.
for %%V in (APP_ENV APP_HOST APP_PORT APP_LOG_LEVEL FRONTEND_ORIGINS DATA_DIR DATABASE_URL LOG_FILE MAX_UPLOAD_BYTES MAX_IMAGE_PIXELS MAX_IMAGE_EDGE JOB_MAX_CONCURRENCY JOB_THREAD_WORKERS JOB_POLL_INTERVAL_MS OCR_PROVIDER OPENAI_API_KEY OPENAI_PRIMARY_MODEL OPENAI_FALLBACK_MODEL OPENAI_IMAGE_DETAIL OPENAI_REASONING_EFFORT OPENAI_MAX_CALLS_PER_JOB OPENAI_MAX_OUTPUT_TOKENS OPENAI_TIMEOUT_SECONDS OPENAI_MAX_RETRIES OPENAI_PROMPT_VERSION OPENAI_SCHEMA_VERSION HANDWRITING_SEGMENTER_MODE HANDWRITING_MODEL_PATH HANDWRITING_MODEL_VERSION HANDWRITING_THRESHOLD HANDWRITING_TILE_SIZE HANDWRITING_TILE_OVERLAP OPENCV_NUM_THREADS MIN_FEATURE_MATCHES MIN_INLIERS MIN_INLIER_RATIO MAX_REPROJECTION_ERROR_PX MIN_ECC_SCORE MIN_EDGE_OVERLAP MIN_TEMPLATE_CONFIDENCE GOOGLE_SHEETS_ENABLED GOOGLE_SHEETS_SPREADSHEET_ID GOOGLE_SHEETS_CREDENTIALS_PATH GOOGLE_SHEETS_FRONTEND_BASE_URL GOOGLE_SHEETS_SPREADSHEET_TITLE GOOGLE_SHEETS_EDIT_POLL_INTERVAL_SECONDS GOOGLE_OAUTH_ENABLED GOOGLE_OAUTH_CLIENT_ID GOOGLE_OAUTH_CLIENT_SECRET GOOGLE_PICKER_API_KEY GOOGLE_PICKER_APP_ID AUTH_JWT_SECRET GOOGLE_OAUTH_REDIRECT_URI GOOGLE_OAUTH_FRONTEND_URL AUTH_SESSION_TTL_SECONDS AUTH_STATE_TTL_SECONDS AUTH_SECURE_COOKIE) do set "%%V="

if not exist "backend\.venv\Scripts\python.exe" goto :setup
if not exist "backend\.venv\Scripts\alembic.exe" goto :setup
if not exist "frontend\node_modules" goto :setup
if not exist "frontend\node_modules\.bin\vite.cmd" goto :setup
if not exist "backend\.env" goto :setup
"backend\.venv\Scripts\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)" >nul 2>&1
if errorlevel 1 goto :setup
pushd "backend"
".venv\Scripts\python.exe" -c "import app.main" >nul 2>&1
set "startup_check_error=%errorlevel%"
popd
if not "%startup_check_error%"=="0" goto :setup
goto :start

:setup
echo Preparing the application for its first start...
call "%~dp0setup.bat"
if errorlevel 1 exit /b %errorlevel%

:start
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\dev.ps1"
exit /b %errorlevel%
