"""A directory a task is carried out inside.

Every task is given an empty directory, is allowed to touch only that
directory, and is judged by looking at it afterwards. That is the whole
contract, and it is what makes a task decidable without reading anything a
solver wrote in prose: the verifier reads the filesystem and nothing else.

The root is created fresh per task. A solver that leaves a file behind from an
earlier task cannot pass a later one on residue, and a task that fails cannot
leave a partial file that a rerun would then read as its own work.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path


class SandboxError(RuntimeError):
    """A path was asked for that would leave the sandbox."""


class Sandbox:
    """One task's workspace.

    Paths are relative to the root. A path that escapes the root is refused
    rather than normalised, because a task that tests file layout is worth
    nothing if the layout it tests can point anywhere.
    """

    def __init__(self, root: Path | None = None) -> None:
        self._owned = root is None
        self.root = Path(root) if root is not None else Path(tempfile.mkdtemp(prefix="gembench-"))
        self.root.mkdir(parents=True, exist_ok=True)

    # -- paths ---------------------------------------------------------------

    def path(self, relative: str) -> Path:
        """The absolute path of *relative*, refused if it leaves the root."""
        candidate = (self.root / relative).resolve()
        root = self.root.resolve()
        if candidate != root and root not in candidate.parents:
            raise SandboxError(f"{relative!r} leaves the workspace")
        return candidate

    # -- reads and writes ----------------------------------------------------

    def write(self, relative: str, content: str) -> None:
        target = self.path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def read(self, relative: str) -> str:
        target = self.path(relative)
        if not target.is_file():
            return ""
        return target.read_text(encoding="utf-8", errors="replace")

    def exists(self, relative: str) -> bool:
        return self.path(relative).exists()

    def is_dir(self, relative: str) -> bool:
        return self.path(relative).is_dir()

    def remove(self, relative: str) -> bool:
        """Delete a file or an empty directory; report whether it was there."""
        target = self.path(relative)
        if target.is_dir():
            shutil.rmtree(target)
            return True
        if target.is_file():
            target.unlink()
            return True
        return False

    def move(self, source: str, destination: str) -> None:
        """Move a file, creating the destination directory when it is absent."""
        src = self.path(source)
        dst = self.path(destination)
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.replace(dst)

    # -- reading the result --------------------------------------------------

    def inventory(self) -> list[dict]:
        """Every file under the root, as relative paths in sorted order."""
        root = self.root.resolve()
        entries: list[dict] = []
        for item in sorted(root.rglob("*")):
            if item.is_file():
                entries.append(
                    {
                        "path": item.relative_to(root).as_posix(),
                        "bytes": item.stat().st_size,
                        "is_dir": False,
                    }
                )
            elif item.is_dir():
                entries.append(
                    {"path": item.relative_to(root).as_posix(), "bytes": 0, "is_dir": True}
                )
        return entries

    def names(self) -> list[str]:
        """The relative path of every file, which is what most checks read."""
        return [entry["path"] for entry in self.inventory() if not entry["is_dir"]]

    def cleanup(self) -> None:
        """Remove a directory this instance created, and nothing else."""
        if self._owned:
            shutil.rmtree(self.root, ignore_errors=True)
