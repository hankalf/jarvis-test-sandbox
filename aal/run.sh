#!/usr/bin/env bash
# Start AAL. Usage: ./run.sh
set -euo pipefail
cd "$(dirname "$0")"

if [ -f .env ]; then
  set -a; . ./.env; set +a
fi

if [ ! -d .venv ]; then
  echo "==> creating .venv"
  python3 -m venv .venv
  ./.venv/bin/pip install --quiet --upgrade pip
  ./.venv/bin/pip install --quiet -r requirements.txt
fi

if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  echo "!! ANTHROPIC_API_KEY is not set — the UI will load but chat will fail."
  echo "   cp .env.example .env and put your key in it."
fi

PORT="${AAL_PORT:-8420}"
echo "==> AAL on http://127.0.0.1:${PORT}"
exec ./.venv/bin/python -m uvicorn server.main:app --host "${AAL_HOST:-127.0.0.1}" --port "${PORT}"
