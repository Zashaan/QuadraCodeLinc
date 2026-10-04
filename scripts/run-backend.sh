#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
set -a
if [[ -f .env ]]; then source ./.env; fi
set +a
cd backend
exec .venv/bin/uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
