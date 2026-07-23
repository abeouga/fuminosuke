#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
BACKEND_DIR="$PROJECT_ROOT/backend"
FRONTEND_DIR="$PROJECT_ROOT/frontend"
PYTHON="$BACKEND_DIR/.venv/bin/python"

if [ ! -x "$PYTHON" ]; then python -m venv "$BACKEND_DIR/.venv"; fi
"$PYTHON" -m pip install --requirement "$BACKEND_DIR/requirements.lock"
"$PYTHON" -m pip install --no-deps --editable "$BACKEND_DIR"
if [ ! -f "$BACKEND_DIR/.env" ]; then cp "$BACKEND_DIR/.env.example" "$BACKEND_DIR/.env"; fi
cd "$BACKEND_DIR"
"$PYTHON" -m alembic upgrade head
cd "$FRONTEND_DIR"
npm ci
echo 'セットアップが完了しました。scripts/dev.sh で起動できます。'
