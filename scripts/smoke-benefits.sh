#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
backend/.venv/bin/python scripts/with-timeout.py 30 backend/.venv/bin/python scripts/smoke-benefits.py
