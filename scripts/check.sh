#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
(
  cd backend
  .venv/bin/pytest
  .venv/bin/ruff check .
  .venv/bin/ruff format --check .
  .venv/bin/mypy
)
(
  cd voice-gateway
  npm test
  npm run lint
  npm run typecheck
  npm run build
)
