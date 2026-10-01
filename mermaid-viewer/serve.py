#!/usr/bin/env python3
"""Static file server for the Mermaid Viewer.

The viewer's source picker is fed by a manifest of the diagrams held in this
folder and in the subfolders beside it. Two things serve that manifest:

  * a dynamic /sources.json route, recomputed per request, used when the viewer
    is opened through this server (serve.sh);
  * a generated sources.json file on disk, refreshed here, so the viewer also
    works behind any plain static host (python -m http.server, nginx, ...)
    that cannot compute a listing for us.

Everything else is served as an ordinary static file.
"""
from __future__ import annotations

import json
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
#: A diagram is a diagram whatever an editor appended to it: `sequence.mmd.md`
#: is the same file as `sequence.mmd`, named so a markdown preview renders it.
DIAGRAM_SUFFIXES = (".mmd", ".mermaid")
MARKDOWN_SUFFIX = ".md"
#: Never walked. They hold no diagram, and one of them holds a 3.4 MB bundle.
SKIP_DIRECTORIES = frozenset({"vendor", "node_modules", "__pycache__", "images"})
MANIFEST_ROUTE = "/sources.json"
MANIFEST_FILE = ROOT / "sources.json"
DEFAULT_PORT = 8765


def is_diagram_name(name: str) -> bool:
    """True for `x.mmd`, `x.mermaid`, `x.mmd.md` and `x.mermaid.md`."""
    lowered = name.lower()
    if lowered.endswith(MARKDOWN_SUFFIX):
        lowered = lowered[: -len(MARKDOWN_SUFFIX)]
    return lowered.endswith(DIAGRAM_SUFFIXES)


def diagram_directories() -> Iterator[Path]:
    """This folder, then each of its subfolders, in a stable order."""
    yield ROOT
    for entry in sorted(ROOT.iterdir(), key=lambda item: item.name.lower()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        if entry.name in SKIP_DIRECTORIES:
            continue
        yield entry


def discover_sources() -> list[str]:
    """Every diagram beside or below this script, as viewer relative paths."""
    names = [
        entry.relative_to(ROOT).as_posix()
        for directory in diagram_directories()
        for entry in sorted(directory.iterdir())
        if entry.is_file() and is_diagram_name(entry.name)
    ]
    return sorted(names, key=str.lower)


def manifest_payload() -> bytes:
    return json.dumps(discover_sources()).encode("utf-8")


def write_manifest() -> None:
    """Mirror the manifest to disk so static hosts can serve it as a real file.

    Writes atomically and only when the contents actually changed, so a file
    watcher or a browser caching layer is not woken up on every request.
    """
    payload = manifest_payload()
    try:
        if MANIFEST_FILE.exists() and MANIFEST_FILE.read_bytes() == payload:
            return
        temp = MANIFEST_FILE.with_name(MANIFEST_FILE.name + ".tmp")
        temp.write_bytes(payload)
        temp.replace(MANIFEST_FILE)
    except OSError as error:
        print(f"warning: could not write {MANIFEST_FILE.name}: {error}", file=sys.stderr)


class ViewerHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def do_GET(self) -> None:  # noqa: N802 - name mandated by the base class
        if urlparse(self.path).path == MANIFEST_ROUTE:
            self._send_manifest()
            return
        super().do_GET()

    def _send_manifest(self) -> None:
        write_manifest()
        payload = manifest_payload()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt: str, *args) -> None:
        if self.path.startswith(MANIFEST_ROUTE):
            return
        super().log_message(fmt, *args)


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    server = ThreadingHTTPServer(("127.0.0.1", port), ViewerHandler)
    write_manifest()
    sources = discover_sources()
    print(f"Mermaid Viewer: http://127.0.0.1:{port}/index.html")
    print(f"Diagrams found: {', '.join(sources) if sources else 'none'}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
