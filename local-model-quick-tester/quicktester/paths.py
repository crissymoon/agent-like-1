"""Where things are, and how a path may be written down.

Two jobs, and they are together because both are about the boundary between this
checkout and the machine it is on.

Finding the repository root is a search rather than a fixed number of `.parent`
calls, so the tool keeps working if it is moved or if a checkout nests it
somewhere unexpected.

Writing a path down is the same rule the PHP record writers use
(`lib/PathRecord.php`) and the same one `tools/normalize_paths.py` applies to
files already written: a path inside the repository is relative to it, a path
under the temporary directory is `<tmp>/...`, a path under the home directory is
`~/...`. Nothing else is written, because a transcript that names the account it
was made under is a transcript that cannot leave the machine. The repository
enforces that from the other side as well: `tools/security/patterns.py` refuses
`/Users/<name>/` and its Windows equivalent, so a path recorded carelessly is a
path that blocks a commit rather than one that quietly ships.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

#: A directory that holds both of these is this project. Named rather than
#: guessed, because `.git` alone would match any checkout and this tool is run
#: from inside one.
ROOT_MARKERS = ("config.php", "lib")


def repository_root(start: Path | None = None) -> Path:
    """The checkout this tool sits in, found by walking up from here."""
    here = (start or Path(__file__).resolve()).resolve()
    for candidate in (here, *here.parents):
        if all((candidate / marker).exists() for marker in ROOT_MARKERS):
            return candidate
    # A tool run from somewhere else entirely still needs a root to be relative
    # to, and this file's own position is the best answer available.
    return here.parents[1]


def expand(text: str | os.PathLike[str] | None) -> Path | None:
    """A path from a setting or the command line, with `~` and `$VARS` resolved."""
    if text is None or str(text).strip() == "":
        return None
    return Path(os.path.expandvars(os.path.expanduser(str(text)))).resolve()


def record(path: Path | str | None, root: Path | None = None) -> str:
    """A path as it may be written into a file that leaves this machine.

    Longest match first, because the repository usually sits under the home
    directory and the more specific rule has to win.
    """
    if path is None:
        return "not recorded"
    value = Path(path)
    try:
        resolved = value.resolve()
    except OSError:
        resolved = value

    root = (root or repository_root()).resolve()
    candidates = [
        (root, ""),
        (Path(tempfile.gettempdir()).resolve(), "<tmp>"),
        (Path.home().resolve(), "~"),
    ]
    candidates = sorted(candidates, key=lambda pair: -len(str(pair[0])))

    for prefix, marker in candidates:
        try:
            relative = resolved.relative_to(prefix)
        except ValueError:
            continue
        joined = f"{marker}/{relative}" if marker else str(relative)
        return joined if joined else "."

    # Outside all three. Writing an absolute path would describe the machine, so
    # the name alone is recorded and the reader is told it is a name.
    return f"<elsewhere>/{resolved.name}"


def is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False
