#!/usr/bin/env bash
# Serve the mermaid viewer locally so relative fetches of sources.json
# and *.mmd files work (file:// blocks them). Usage: ./serve.sh [port]
set -euo pipefail

PORT="${1:-8765}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Mermaid Viewer: http://127.0.0.1:${PORT}/index.html"
echo "Press Ctrl+C to stop."
exec python3 -m http.server "${PORT}" --bind 127.0.0.1 --directory "${ROOT}"
