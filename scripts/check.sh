#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
(
  cd backend
  .venv/bin/python ../scripts/with-timeout.py 90 .venv/bin/pytest
  .venv/bin/python ../scripts/with-timeout.py 30 .venv/bin/ruff check .
  .venv/bin/python ../scripts/with-timeout.py 30 .venv/bin/ruff format --check .
  .venv/bin/python ../scripts/with-timeout.py 60 .venv/bin/mypy
)
(
  cd voice-gateway
  ../backend/.venv/bin/python ../scripts/with-timeout.py 90 npm test
  ../backend/.venv/bin/python ../scripts/with-timeout.py 30 npm run lint
  ../backend/.venv/bin/python ../scripts/with-timeout.py 60 npm run typecheck
  ../backend/.venv/bin/python ../scripts/with-timeout.py 60 npm run build
)
./scripts/smoke-benefits.sh
