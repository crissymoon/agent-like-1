#!/usr/bin/env bash
#
# Guarded shell runner.
#
# This is the only script the agent may trigger. It pins the working folder,
# keeps the Python path local, and defers all validation to sh_runner.py, which
# scans the request before anything is staged.
#
#   ./sh_runner.sh --request request.json
#   ./sh_runner.sh --stdin
#   ./sh_runner.sh --scan "pytest -q"
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

export PYTHONPATH="$SCRIPT_DIR:${PYTHONPATH:-}"
PY="${PYTHON:-python3}"

exec "$PY" security/sh_runner.py "$@"
