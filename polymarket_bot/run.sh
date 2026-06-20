#!/usr/bin/env bash
set -euo pipefail

# Polymarket Paper Trading Bot launcher.
cd "$(dirname "$0")"

if [ ! -f .env ]; then
  echo "No .env found — copying .env.example. Edit it before going further."
  cp .env.example .env
fi

# Load env vars for HOST/PORT defaults.
set -a; source .env; set +a

exec uvicorn app.main:app --host "${HOST:-0.0.0.0}" --port "${PORT:-8000}"
