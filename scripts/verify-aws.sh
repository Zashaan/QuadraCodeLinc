#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
set -a
if [[ -f .env ]]; then source ./.env; fi
set +a
exec backend/.venv/bin/python scripts/verify-aws.py
