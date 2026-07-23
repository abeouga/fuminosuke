#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
BACKEND_DIR="$PROJECT_ROOT/backend"
FRONTEND_DIR="$PROJECT_ROOT/frontend"
PYTHON="$BACKEND_DIR/.venv/bin/python"

if [ ! -x "$PYTHON" ]; then
  echo 'backend/.venv がありません。README の初回セットアップを実行してください。' >&2
  exit 1
fi

mkdir -p "$BACKEND_DIR/data/logs"
cd "$BACKEND_DIR"
"$PYTHON" -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 >data/logs/dev-backend.out.log 2>data/logs/dev-backend.err.log &
BACKEND_PID=$!
cd "$FRONTEND_DIR"
npm run dev -- --host 127.0.0.1 >../backend/data/logs/dev-frontend.out.log 2>../backend/data/logs/dev-frontend.err.log &
FRONTEND_PID=$!
trap 'kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true' EXIT INT TERM
echo 'バックエンド: http://127.0.0.1:8000/docs'
echo 'フロントエンド: http://127.0.0.1:5173'
wait "$BACKEND_PID" "$FRONTEND_PID"
