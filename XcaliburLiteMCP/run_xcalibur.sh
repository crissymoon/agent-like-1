#!/usr/bin/env bash
#
# XcaliburLite launcher.
#
# Pins the working folder to this directory, ensures the Python dependencies are
# present, then hands off to the TUI. The workspace it opens on defaults to the
# working folder beside this script; point it at a project with
# XCALIBUR_WORKSPACE (XCALIBUR_TARGET_ROOT is accepted as the older name). The
# TUI creates the harness store inside the workspace and brings up llama-server
# and the review dashboard itself unless told not to.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

export PYTHONPATH="$SCRIPT_DIR:${PYTHONPATH:-}"
PY="${PYTHON:-python3}"

if ! command -v "$PY" >/dev/null 2>&1; then
  echo "[xcalibur] python interpreter not found: $PY" >&2
  exit 1
fi

if ! "$PY" -c "import flask, requests, dotenv, rich, prompt_toolkit" >/dev/null 2>&1; then
  echo "[xcalibur] installing python dependencies..."
  "$PY" -m pip install -q -r requirements.txt
fi

WORKSPACE_DIR="${XCALIBUR_WORKSPACE:-${XCALIBUR_TARGET_ROOT:-$SCRIPT_DIR/workspace}}"
mkdir -p "$WORKSPACE_DIR"

exec "$PY" cli.py "$@"
