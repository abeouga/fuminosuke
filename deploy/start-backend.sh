#!/bin/sh
set -eu
if [ "${APP_ENV:-container}" != "cloud" ]; then
  python -m alembic upgrade head
else
  # Backend has no published port; only the private Compose proxy may reach it.
  exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 --no-access-log \
    --proxy-headers --forwarded-allow-ips '*'
fi
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 --no-access-log
