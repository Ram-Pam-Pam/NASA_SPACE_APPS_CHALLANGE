#!/bin/sh
# Starts Corridor Watch (Tabasco, Portuguese Bend, Bayou Corne - see README.md).
# Port: 8000, or another one with   PORT=8001 ./start.sh
PORT="${PORT:-8000}"
cd "$(dirname "$0")/backend" || exit 1
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Port $PORT jest zajety (dziala tam inny serwer, np. z glownego projektu)."
  echo "Zatrzymaj go albo uruchom na innym porcie:  PORT=8001 ./start.sh"
  exit 1
fi
if [ ! -d .venv ]; then
  python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt || exit 1
fi
echo "Strona: http://127.0.0.1:$PORT/view3d.html?view=2d#tabasco_mexico"
exec .venv/bin/uvicorn app.main:app --port "$PORT"
