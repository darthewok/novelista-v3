#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p .run

if [ -f .run/pid ] && [ -f .run/port ]; then
  OLDPID=$(cat .run/pid)
  OLDPORT=$(cat .run/port)
  if kill -0 "$OLDPID" 2>/dev/null; then
    echo "[novelista-v3] already running on PID $OLDPID (http://127.0.0.1:$OLDPORT)"
    open "http://127.0.0.1:$OLDPORT" 2>/dev/null || xdg-open "http://127.0.0.1:$OLDPORT" 2>/dev/null || true
    exit 0
  fi
fi

PY=$(command -v python3.14 || command -v python3.13 || command -v python3.12 || command -v python3.11 || command -v python3)
if [ ! -x backend/.venv/bin/python ]; then
  "$PY" -m venv backend/.venv
  backend/.venv/bin/python -m pip install -q -r backend/requirements.txt
fi

PORT=8000
for p in $(seq 8000 8010); do
  if ! lsof -iTCP:"$p" -sTCP:LISTEN >/dev/null 2>&1; then
    PORT=$p
    break
  fi
done

[ -f .run/server.log ] && mv -f .run/server.log .run/server.prev.log
echo "$PORT" > .run/port
(
  cd backend
  nohup .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" > ../.run/server.log 2>&1 &
  echo $! > ../.run/pid
)

for _ in $(seq 1 30); do
  if curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$PORT/api/health" | grep -q 200; then
    open "http://127.0.0.1:$PORT" 2>/dev/null || xdg-open "http://127.0.0.1:$PORT" 2>/dev/null || true
    exit 0
  fi
  sleep 1
done

echo "[novelista-v3] failed to start within 30s"
exit 1
