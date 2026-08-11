#!/usr/bin/env bash
set -euo pipefail

# Wait for Postgres to accept connections before migrating.
echo "Waiting for database at ${POSTGRES_HOST:-db}:${POSTGRES_PORT:-5432} ..."
for i in $(seq 1 60); do
  if python -c "
import socket, os, sys
s = socket.socket()
s.settimeout(2)
try:
    s.connect((os.environ.get('POSTGRES_HOST','db'), int(os.environ.get('POSTGRES_PORT','5432'))))
    sys.exit(0)
except Exception:
    sys.exit(1)
" 2>/dev/null; then
    echo "Database is up."
    break
  fi
  sleep 1
done

echo "Running migrations ..."
alembic upgrade head

echo "Starting API ..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
