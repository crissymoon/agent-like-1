#!/usr/bin/env bash
#
# XcaliburLite as an MCP server.
#
# An MCP host starts this file and speaks JSON-RPC over standard input and
# output. It pins the working folder and keeps the Python path local, so the
# server runs the same code the terminal does no matter where the host was
# started from. The reads are confined to the target root by the adapter and the
# single write tool stages into the shadow copy, so nothing here touches the
# live tree.
#
#   ./xcalibur_mcp.sh               # serve on stdio, for an MCP host
#   ./xcalibur_mcp.sh --tools       # print the advertised tools and exit
#   ./xcalibur_mcp.sh --self-check  # check the handshake and reads, then exit
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

export PYTHONPATH="$SCRIPT_DIR:${PYTHONPATH:-}"
PY="${PYTHON:-python3}"

if ! "$PY" -c "import requests, dotenv" >/dev/null 2>&1; then
  echo "[xcalibur] installing python dependencies..." >&2
  "$PY" -m pip install -q -r requirements.txt
fi

exec "$PY" mcp_server.py "$@"
