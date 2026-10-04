#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
exec backend/.venv/bin/python scripts/smoke-benefits.py
