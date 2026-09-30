"""Shorten the machine paths already written into recorded runs.

The record writers shorten paths as they write them (`lib/PathRecord.php`), and
a record written before that existed still carries the checkout location, the
account name and the temporary directory it ran in. This rewrites those in
place, with the same rules the writer uses, so a checkout of this repository
carries no description of the machine it was recorded on:

    under the repository   results/agent/baseline/events.ndjson
    under the temp dir     <tmp>/gemma-agent-workspace
    under the home dir     ~/Documents/elsewhere

    python3 tools/normalize_paths.py --check     # report, change nothing
    python3 tools/normalize_paths.py --write     # rewrite the files

The default subject is every tracked file that is not a binary. A path may be
given instead, which is how a single record is fixed. `--check` exits non-zero
when something would change, so it can be used as a check in a hook or a test.

It is idempotent: a shortened path is not absolute, so running it twice leaves
the second run with nothing to do.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path


#: Extensions that are never text. A weight file or an image has no path in it
#: to shorten, and rewriting bytes would corrupt the file.
BINARY_SUFFIXES: frozenset[str] = frozenset(
    {
        ".gguf", ".ggml", ".safetensors", ".onnx", ".pt", ".pth", ".bin", ".h5",
        ".ckpt", ".zip", ".tar", ".gz", ".tgz", ".7z", ".dmg", ".iso",
        ".jpg", ".jpeg", ".png", ".gif", ".heic", ".tif", ".webp", ".mov", ".mp4",
    }
)


def repository_root() -> Path:
    """The working tree this tool is running inside, as git resolves it."""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], capture_output=True, check=False
    )
    if result.returncode != 0:
        raise SystemExit("normalize_paths: not inside a git working tree")
    return Path(result.stdout.decode().strip())


def _prefixes(root: Path) -> list[tuple[str, str]]:
    """The prefixes to shorten, longest first, each with what replaces it."""
    candidates = [
        (str(root), ""),
        (tempfile.gettempdir(), "<tmp>"),
        (str(Path.home()), "~"),
    ]
    cleaned = [(prefix.rstrip("/"), marker) for prefix, marker in candidates if prefix.rstrip("/")]

    # Longest first, or the account rule would take a path that belongs to the
    # repository and shorten it to something under the home directory instead of
    # something relative to the tree it is in.
    return sorted(cleaned, key=lambda pair: -len(pair[0]))


def shorten(text: str, root: Path) -> tuple[str, int]:
    """Every machine path in one file's text, shortened. Returns the text and a count."""
    changed = 0
    for prefix, marker in _prefixes(root):
        # The separator is part of the match, so the replacement decides whether
        # there is one. An empty marker is what turns an absolute path into a
        # path relative to the repository, and it has to consume the slash to do
        # it. The second pattern catches a prefix that is the whole value, which
        # is how a temporary directory is recorded when nothing was written
        # under it yet, and the lookahead keeps it from eating the start of a
        # longer word.
        patterns = (
            (re.compile(re.escape(prefix) + r"[/\\]"), marker + "/" if marker else ""),
            (re.compile(re.escape(prefix) + r"(?=[\"'\s,}\]<>])"), marker or "."),
        )
        for pattern, replacement in patterns:
            text, count = pattern.subn(replacement, text)
            changed += count
    return text, changed


def _tracked_files(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=str(root), capture_output=True, check=True
    )
    return [
        root / name
        for name in result.stdout.decode().split("\0")
        if name and Path(name).suffix.lower() not in BINARY_SUFFIXES
    ]


def _is_text(path: Path) -> bool:
    try:
        head = path.open("rb").read(4096)
    except OSError:
        return False
    return b"\x00" not in head


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="normalize_paths",
        description="Shorten machine paths in recorded runs to repository relative form.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="report, write nothing (default)")
    mode.add_argument("--write", action="store_true", help="rewrite the files in place")
    parser.add_argument(
        "paths",
        nargs="*",
        help="files to read instead of every tracked text file",
    )
    args = parser.parse_args(argv)

    root = repository_root()
    targets = [Path(raw) for raw in args.paths] if args.paths else _tracked_files(root)

    touched: list[tuple[str, int]] = []
    for path in targets:
        if not path.is_file() or not _is_text(path):
            continue
        original = path.read_text(encoding="utf-8", errors="strict")
        shortened, count = shorten(original, root)
        if count == 0:
            continue
        try:
            relative = str(path.resolve().relative_to(root))
        except ValueError:
            relative = str(path)
        touched.append((relative, count))
        if args.write:
            path.write_text(shortened, encoding="utf-8")

    for relative, count in touched:
        print(f"normalize_paths: {relative}  {count} path(s)")

    if not touched:
        print("normalize_paths: nothing to shorten, every recorded path is already relative")
        return 0

    print(f"normalize_paths: {'rewrote' if args.write else 'would rewrite'} {len(touched)} file(s)")
    return 0 if args.write else 1


if __name__ == "__main__":
    raise SystemExit(main())

