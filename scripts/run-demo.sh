#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
command -v node >/dev/null || { echo 'Install Node.js 22+ and npm first.' >&2; exit 1; }
command -v npm >/dev/null || { echo 'Install npm first.' >&2; exit 1; }
node -e 'if (Number(process.versions.node.split(".")[0]) < 22) process.exit(1)' || { echo 'Node.js 22+ is required.' >&2; exit 1; }
for candidate in "${ABE_PYTHON:-python3}" python3.14 python3.13 python3.12; do
  if command -v "$candidate" >/dev/null && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 12))' 2>/dev/null; then
    exec "$candidate" scripts/run-demo.py "$@"
  fi
done
echo 'Install Python 3.12+ (with venv), or set ABE_PYTHON to its executable.' >&2
exit 1
