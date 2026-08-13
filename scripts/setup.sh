#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
BACKEND_DIR="$PROJECT_ROOT/backend"
FRONTEND_DIR="$PROJECT_ROOT/frontend"
PYTHON="$BACKEND_DIR/.venv/bin/python"

if [ ! -x "$PYTHON" ]; then python -m venv "$BACKEND_DIR/.venv"; fi
"$PYTHON" -m pip install --requirement "$BACKEND_DIR/requirements.lock"
if [ "${LLM_OCR_INSTALL_PADDLE:-0}" = "1" ]; then
  if ! "$PYTHON" -m pip install --requirement "$BACKEND_DIR/requirements-paddle.txt"; then
    echo '警告: 任意機能のPaddleOCRを導入できませんでした。OpenCVのレイアウト編集とOpenAI OCRは引き続き利用できます。'
  fi
else
  echo '任意機能のPaddleOCRは省略します。必要な場合は LLM_OCR_INSTALL_PADDLE=1 を設定してください。'
fi
"$PYTHON" -m pip install --no-deps --editable "$BACKEND_DIR"
if [ ! -f "$BACKEND_DIR/.env" ]; then
  cp "$BACKEND_DIR/.env.example" "$BACKEND_DIR/.env"
  echo 'backend/.env を安全なmock OCR設定で作成しました。外部OCRを使う前に OCR_PROVIDER と OPENAI_API_KEY を設定してください。'
fi
if [ ! -f "$BACKEND_DIR/data/app.db" ] && [ -f "$BACKEND_DIR/bootstrap/templates/app.db" ]; then
  cp -R "$BACKEND_DIR/bootstrap/templates/." "$BACKEND_DIR/data/"
  echo 'Installed the versioned active-template bootstrap data.'
fi
cd "$BACKEND_DIR"
"$PYTHON" -m alembic upgrade head
cd "$FRONTEND_DIR"
npm ci
echo 'セットアップが完了しました。scripts/dev.sh で起動できます。'
