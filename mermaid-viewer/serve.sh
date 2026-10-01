#!/usr/bin/env bash
# Serve the mermaid viewer locally. A small Python server exposes a dynamic
# /sources.json so the source picker always lists every diagram in this folder
# and in the diagrams folder beside it. Usage: ./serve.sh [port]
set -euo pipefail

PORT="${1:-8765}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

exec python3 "${ROOT}/serve.py" "${PORT}"
