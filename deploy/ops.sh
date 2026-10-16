#!/bin/sh
set -eu
test "$(id -u)" = 0
cd /opt/llmocr
compose() {
  docker compose --env-file /etc/llmocr/cloud.env -p fuminosuke-beta \
    -f deploy/compose.yaml -f deploy/compose.cloud.yaml -f deploy/compose.ops.yaml "$@"
}
start_runtime() {
  if test -f /etc/llmocr/ai-enabled; then
    docker compose --env-file /etc/llmocr/cloud.env -p fuminosuke-beta \
      -f deploy/compose.yaml -f deploy/compose.cloud.yaml -f deploy/compose.ai.yaml \
      up -d --no-build backend
  else
    compose up -d --no-build backend
  fi
}
case "${1:-}" in
  backup)
    # Lease acquisition in the one-shot process additionally refuses any surviving API writer.
    # A failed backup leaves AI/runtime stopped for operator investigation.
    compose stop -t 120 backend
    compose run --rm --no-deps backend python -m app.cloud.maintenance backup
    start_runtime
    ;;
  init-journal)
    compose stop -t 120 backend
    compose run --rm --no-deps backend python -m app.cloud.maintenance init-journal
    ;;
  *) echo "Supported operations: backup, init-journal" >&2; exit 2 ;;
esac
