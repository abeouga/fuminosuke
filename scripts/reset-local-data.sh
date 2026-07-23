#!/usr/bin/env sh
set -eu

if [ "${1:-}" != 'RESET-LOCAL-DATA' ]; then
  echo '完全初期化には RESET-LOCAL-DATA を引数に指定してください。' >&2
  exit 1
fi

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
DATA_DIR="$PROJECT_ROOT/backend/data"

case "$DATA_DIR" in
  "$PROJECT_ROOT"/*) ;;
  *) echo '安全検査に失敗したため、データを削除しません。' >&2; exit 1 ;;
esac

mkdir -p "$DATA_DIR"
find "$DATA_DIR" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
: > "$DATA_DIR/.gitkeep"
echo "ローカルデータをリセットしました: $DATA_DIR"
