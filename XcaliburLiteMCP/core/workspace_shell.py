"""
Workspace navigator.

A small, confined shell for the review terminal: ls, cd, pwd, cat, and file
resolution, with a hard boundary. The current directory is always inside
``NAV_ROOT``; a ``cd`` that would leave it (``..`` above the root, an absolute
path elsewhere, or ``~``) is refused rather than clamped, so the user always
knows exactly where they are. The harness store inside the workspace is refused
as well: the folders the agent stages into are not the user's code. Resolution
of a typed name goes through the interpreter, so an ambiguous or missing name
reports itself instead of guessing.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from config import CAT_MAX_LINES, HARNESS_DIR, IGNORE_DIRS, NAV_ROOT
from core import adapter
from core.interpreter import FileIndex, Resolution


class WorkspaceError(Exception):
    """Raised when a navigation request would leave the workspace or is invalid."""


@dataclass
class Entry:
    name: str
    rel_path: str
    kind: str          # "file" | "directory"
    size: int = 0


class Navigator:
    def __init__(self, root: Path | None = None, index: FileIndex | None = None,
                 harness: Path | None = None) -> None:
        self.root = (root or NAV_ROOT).resolve()
        self.harness = (harness or HARNESS_DIR).resolve()
        self.index = index or FileIndex(self.root)
        self.cwd = ""      # workspace-relative; "" is the root

    # -- path handling -----------------------------------------------------
    def _within(self, target: str) -> tuple[Path, str]:
        """Resolve a user path to an absolute path inside the root, or refuse."""
        raw = (target or "").strip()
        if raw.startswith("~"):
            raise WorkspaceError("cannot leave the workspace: ~ is not allowed")
        base = (self.root / self.cwd) if self.cwd else self.root
        candidate = Path(raw)
        combined = candidate if candidate.is_absolute() else base / raw
        resolved = combined.resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise WorkspaceError(f"outside the workspace: {target}")
        if resolved == self.harness or self.harness in resolved.parents:
            raise WorkspaceError("the harness store is not part of the workspace")
        rel = "" if resolved == self.root else str(resolved.relative_to(self.root))
        return resolved, rel

    def pwd(self) -> str:
        return self.cwd or "."

    def prompt_path(self) -> str:
        return f"~/{self.cwd}" if self.cwd else "~"

    def cd(self, target: str = "") -> str:
        abs_path, rel = self._within(target or ".")
        if not abs_path.exists():
            raise WorkspaceError(f"no such directory: {target}")
        if not abs_path.is_dir():
            raise WorkspaceError(f"not a directory: {target}")
        self.cwd = rel
        return self.pwd()

    # -- listing and reading ----------------------------------------------
    def ls(self, target: str = "", include_hidden: bool = False) -> list[Entry]:
        abs_path, rel = self._within(target or ".")
        if not abs_path.exists():
            raise WorkspaceError(f"no such file or directory: {target}")
        if abs_path.is_file():
            return [Entry(abs_path.name, rel, "file", abs_path.stat().st_size)]
        entries: list[Entry] = []
        for child in sorted(abs_path.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
            if child.name.startswith(".") and not include_hidden:
                continue
            if child.is_dir() and child.name in IGNORE_DIRS:
                continue
            child_rel = str(child.relative_to(self.root))
            kind = "directory" if child.is_dir() else "file"
            size = child.stat().st_size if child.is_file() else 0
            entries.append(Entry(child.name + ("/" if kind == "directory" else ""), child_rel, kind, size))
        return entries

    def cat(self, target: str) -> tuple[str, int, bool]:
        """Read a file inside the workspace. Returns (text, total_lines, truncated)."""
        abs_path, rel = self._within(target)
        if not abs_path.exists() or abs_path.is_dir():
            raise WorkspaceError(f"not a file: {target}")
        if abs_path.stat().st_size > adapter.MAX_FILE_BYTES:
            raise WorkspaceError(f"file too large to display: {rel}")
        try:
            text = abs_path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise WorkspaceError(f"not a text file: {rel}") from exc
        lines = text.splitlines(keepends=True)
        if len(lines) > CAT_MAX_LINES:
            return "".join(lines[:CAT_MAX_LINES]), len(lines), True
        return text, len(lines), False

    # -- resolution --------------------------------------------------------
    def resolve(self, fragment: str) -> Resolution:
        return self.index.resolve(fragment, cwd=self.cwd)

    def resolve_many(self, message: str, limit: int = 8) -> list[Resolution]:
        return self.index.mentions(message, cwd=self.cwd, limit=limit)
