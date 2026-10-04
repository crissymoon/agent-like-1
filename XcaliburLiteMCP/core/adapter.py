"""
The adapter.

Turns a codebase into prompt-friendly text the model can operate on. It is the
single place that decides which files are visible, how large a read is, and how
that read is framed for the model. Everything upstream (the chunker, the
orchestrator) asks the adapter rather than touching the filesystem directly.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from config import IGNORE_DIRS, MAX_FILE_BYTES, MAX_ROWS, TARGET_ROOT, TEXT_SUFFIXES


class AdapterError(Exception):
    """Raised when a request would escape the target root or is otherwise unsafe."""


@dataclass
class FileEntry:
    rel_path: str
    kind: str          # "file" | "directory"
    size: int = 0


@dataclass
class FileRead:
    rel_path: str
    content: str
    total_lines: int
    truncated: bool = False


@dataclass
class ContextBundle:
    """Prompt-ready text plus the files that were actually included."""
    text: str
    files: list[str] = field(default_factory=list)
    omitted: list[str] = field(default_factory=list)


def resolve(rel_path: str, root: Path | None = None) -> Path:
    """Resolve a path and refuse anything outside the target root."""
    base = (root or TARGET_ROOT).resolve()
    candidate = (base / rel_path).resolve() if not os.path.isabs(rel_path) else Path(rel_path).resolve()
    if candidate != base and base not in candidate.parents:
        raise AdapterError(f"path escapes target root: {rel_path}")
    return candidate


def relative(path: Path, root: Path | None = None) -> str:
    base = (root or TARGET_ROOT).resolve()
    try:
        return str(path.resolve().relative_to(base))
    except ValueError:
        return str(path)


def _is_text(path: Path) -> bool:
    if path.suffix.lower() in TEXT_SUFFIXES:
        return True
    if path.suffix == "":
        try:
            return path.read_bytes()[:1024].find(b"\x00") == -1
        except OSError:
            return False
    return False


def find_files(pattern: str, root: Path | None = None, limit: int = MAX_ROWS) -> list[FileEntry]:
    """Glob the target tree for matching files, ignoring heavy directories."""
    base = root or TARGET_ROOT
    matches: list[FileEntry] = []
    for path in sorted(base.rglob(pattern)):
        if any(part in IGNORE_DIRS for part in path.parts):
            continue
        if path.is_file():
            matches.append(FileEntry(relative(path, base), "file", path.stat().st_size))
        if len(matches) >= limit:
            break
    return matches


def list_tree(root: Path | None = None, limit: int = MAX_ROWS) -> list[FileEntry]:
    """List the target tree, files before directories, honouring IGNORE_DIRS."""
    base = root or TARGET_ROOT
    entries: list[FileEntry] = []
    for current, dirs, files in os.walk(base):
        dirs[:] = sorted(d for d in dirs if d not in IGNORE_DIRS and not d.startswith(".git"))
        current_path = Path(current)
        for name in sorted(files):
            path = current_path / name
            entries.append(FileEntry(relative(path, base), "file", path.stat().st_size))
            if len(entries) >= limit:
                return entries
    return entries


def read_text(rel_path: str, start_line: int | None = None, end_line: int | None = None,
              root: Path | None = None, max_bytes: int = MAX_FILE_BYTES) -> FileRead:
    """Read a file (optionally a line range) as UTF-8 text."""
    path = resolve(rel_path, root)
    if not path.exists():
        raise AdapterError(f"file not found: {rel_path}")
    if path.is_dir():
        raise AdapterError(f"not a file: {rel_path}")
    if path.stat().st_size > max_bytes:
        raise AdapterError(f"file too large ({path.stat().st_size} bytes): {rel_path}")

    lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    total = len(lines)
    if start_line or end_line:
        first = max(1, start_line or 1) - 1
        last = end_line or total
        lines = lines[first:last]
    return FileRead(relative(path, root), "".join(lines), total)


def read_for_edit(rel_path: str, root: Path | None = None) -> str:
    """Full, untruncated content used by the patcher. Missing files yield ''."""
    path = resolve(rel_path, root)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def build_context(
    rel_paths: list[str],
    root: Path | None = None,
    budget: int | None = None,
    max_file_bytes: int = MAX_FILE_BYTES,
) -> ContextBundle:
    """
    Render a set of files into one prompt-friendly block, stopping at the
    character budget so the model's context window is never overrun.
    """
    from config import CONTEXT_CHAR_BUDGET

    cap = budget or CONTEXT_CHAR_BUDGET
    base = root or TARGET_ROOT
    parts: list[str] = []
    included: list[str] = []
    omitted: list[str] = []
    used = 0

    for rel in rel_paths:
        try:
            read = read_text(rel, root=base, max_bytes=max_file_bytes)
        except AdapterError:
            omitted.append(rel)
            continue
        block = f"<file path=\"{read.rel_path}\" lines=\"{read.total_lines}\">\n{read.content}\n</file>\n"
        if used + len(block) > cap:
            omitted.append(rel)
            continue
        parts.append(block)
        included.append(read.rel_path)
        used += len(block)

    return ContextBundle("\n".join(parts), included, omitted)
