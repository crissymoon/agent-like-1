#!/usr/bin/env bash
#
# NLP smoke test runner.
#
# Checks the interpretation layer -- resolution, chunking, merge, prompt repair,
# the confined navigator, linters, and context assembly -- without loading the
# model. Runs in seconds, so it can gate a session or a benchmark before the
# GGUF is pulled into memory.
#
#   ./nlp_smoke.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

export PYTHONPATH="$SCRIPT_DIR:${PYTHONPATH:-}"
PY="${PYTHON:-python3}"

if ! "$PY" -c "import flask, requests, dotenv, rich, prompt_toolkit" >/dev/null 2>&1; then
  echo "[xcalibur] installing python dependencies..." >&2
  "$PY" -m pip install -q -r requirements.txt
fi

exec "$PY" tools/nlp_smoke.py "$@"
