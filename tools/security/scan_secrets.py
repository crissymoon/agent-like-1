"""Refuses to let a credential or a closed directory reach the remote.

The repository is pushed by one command, and the two ways a push goes wrong are
the same two every time: a key is written into a file because it was quicker
than an environment variable, and a directory that exists for local work is
added by name. Both are checked here, before the commit rather than after the
push, because a secret that has reached a remote is not fixed by a later commit,
it is fixed by rotating the secret.

    python3 tools/security/scan_secrets.py                  # the staged files
    python3 tools/security/scan_secrets.py --tracked        # everything committed
    python3 tools/security/scan_secrets.py --all            # plus untracked, minus ignored
    python3 tools/security/scan_secrets.py --install-hook   # wire it into git

`--install-hook` writes two hooks, because one is not enough. The pre-commit
hook reads the index, which catches a credential as it is written. The pre-push
hook reads everything committed, which catches one that was committed before
either hook existed and would otherwise leave with the next push.

Exit status is zero when clean, one when something was found, and two when the
check could not run, so a hook that cannot run fails the commit rather than
passing it silently.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

try:  # the package
    from .patterns import (
        GENERIC_ASSIGNMENT,
        PROVIDER_PATTERNS,
        SecretPattern,
        is_placeholder,
    )
except ImportError:  # run as a script, beside its own module
    from patterns import (  # type: ignore[no-redef]
        GENERIC_ASSIGNMENT,
        PROVIDER_PATTERNS,
        SecretPattern,
        is_placeholder,
    )

#: Files larger than this are not scanned. A credential is a line of text, and
#: reading a weight file or a bundled artefact to find one is a cost that buys
#: nothing. The limit is generous for source and tight for binaries on purpose.
MAX_FILE_BYTES = 4 * 1024 * 1024

#: Directories that are never worth walking even when git would offer them.
SKIP_DIRECTORIES: frozenset[str] = frozenset({".git", "node_modules", "__pycache__"})


@dataclass(frozen=True)
class Finding:
    """One reason a push should stop."""

    kind: str
    path: str
    line: int
    name: str
    detail: str
    excerpt: str

    def render(self) -> str:
        location = f"{self.path}:{self.line}" if self.line else self.path
        return f"{location}  [{self.name}]  {self.excerpt}\n    {self.detail}"


@dataclass(frozen=True)
class ForbiddenPath:
    """A path that must stay on this machine, and why."""

    prefix: str
    note: str


#: Paths kept out of the repository by name as a second line of defence behind
#: .gitignore. The reason is carried with the rule, because a rule whose reason
#: is lost is a rule somebody deletes.
FORBIDDEN_PATHS: tuple[ForbiddenPath, ...] = (
    ForbiddenPath(
        prefix="paper/",
        note="Draft research notes. Unpublished and full of numbers not meant to leave the machine.",
    ),
    ForbiddenPath(
        prefix="pricing-data/",
        note="Captured vendor pricing and planning notes. Local research input, not repository content.",
    ),
    ForbiddenPath(
        prefix="models/",
        note="Model weights. Nine gigabytes, read-only input, never repository content.",
    ),
)


def mask(value: str) -> str:
    """A credential as it may appear in a report: enough to locate, not to use."""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}{'*' * 6}{value[-2:]} ({len(value)} chars)"


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _looks_binary(data: bytes) -> bool:
    return b"\x00" in data[:4096]


def scan_text(text: str, path: str) -> list[Finding]:
    """Every credential-shaped string in one file's text."""
    findings: list[Finding] = []

    for pattern in (*PROVIDER_PATTERNS, GENERIC_ASSIGNMENT):
        for match in pattern.regex.finditer(text):
            value = match.group(pattern.group)
            if not value or is_placeholder(value):
                continue
            findings.append(
                Finding(
                    kind="secret",
                    path=path,
                    line=_line_of(text, match.start()),
                    name=pattern.name,
                    detail=pattern.note,
                    excerpt=mask(value),
                )
            )

    return findings


def scan_bytes(data: bytes, path: str) -> list[Finding]:
    """One file's bytes, decoded for scanning or skipped as binary."""
    if _looks_binary(data):
        return []
    return scan_text(data.decode("utf-8", errors="replace"), path)


def _git(*args: str, cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", errors="replace").strip())
    return result.stdout.decode("utf-8", errors="replace")


def repository_root(start: Path | None = None) -> Path:
    """The working tree this script is running inside."""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=str(start or HERE),
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("not inside a git working tree")
    return Path(result.stdout.decode("utf-8").strip())


def _staged_paths(root: Path) -> list[str]:
    """Added or modified paths in the index, deletions and renames excluded."""
    output = _git("diff", "--cached", "--name-only", "--diff-filter=d", cwd=root)
    return [line for line in output.splitlines() if line.strip()]


def _tracked_paths(root: Path) -> list[str]:
    return [line for line in _git("ls-files", cwd=root).splitlines() if line.strip()]


def _worktree_paths(root: Path) -> list[str]:
    """Everything git would consider, which is tracked plus untracked-minus-ignored."""
    output = _git("ls-files", "--cached", "--others", "--exclude-standard", cwd=root)
    return [line for line in output.splitlines() if line.strip()]


def _staged_bytes(root: Path, path: str) -> bytes:
    """The content of a staged path, which is the version that would be committed.

    Reading the index rather than the working tree is the whole point of a
    pre-commit check: a file can be edited after it was staged, and the version
    that ships is the staged one.
    """
    result = subprocess.run(
        ["git", "show", f":{path}"],
        cwd=str(root),
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return b""
    return result.stdout


def scan_repository(root: Path, mode: str) -> list[Finding]:
    """Scan one of the three views of the repository.

    `mode` is "staged", "tracked" or "all". The staged view reads the index; the
    other two read the working tree.
    """
    if mode == "staged":
        paths = _staged_paths(root)
    elif mode == "tracked":
        paths = _tracked_paths(root)
    elif mode == "all":
        paths = _worktree_paths(root)
    else:
        raise ValueError(f"unknown scan mode: {mode}")

    findings: list[Finding] = []
    for relative in paths:
        parts = Path(relative).parts
        if any(part in SKIP_DIRECTORIES for part in parts):
            continue
        if mode == "staged":
            data = _staged_bytes(root, relative)
        else:
            absolute = root / relative
            if not absolute.is_file():
                continue
            size = absolute.stat().st_size
            if size == 0 or size > MAX_FILE_BYTES:
                continue
            data = absolute.read_bytes()
        findings.extend(scan_bytes(data, relative))

    return findings


def check_paths(paths: list[str]) -> list[Finding]:
    """The forbidden-path half of the check.

    A path is refused when it is the directory itself or anything beneath it.
    The comparison is on the normalised string so that a leading `./` and a
    trailing slash do not walk past the rule.
    """
    findings: list[Finding] = []
    for raw in paths:
        normalised = raw.replace("\\", "/").strip()
        while normalised.startswith("./"):
            normalised = normalised[2:]
        for rule in FORBIDDEN_PATHS:
            if normalised == rule.prefix.rstrip("/") or normalised.startswith(rule.prefix):
                findings.append(
                    Finding(
                        kind="path",
                        path=normalised,
                        line=0,
                        name="forbidden-path",
                        detail=rule.note,
                        excerpt=f"matches {rule.prefix}",
                    )
                )
                break

    return findings


HOOK_TEMPLATE = """#!/bin/sh
# Installed by tools/security/scan_secrets.py --install-hook.
#
# It scans the staged content, not the working tree, because the staged version
# is the one that would be committed. It also refuses the directories that are
# local by design. Remove this file to uninstall; do not edit it, it is written
# from the script so that the two cannot drift.
set -e
root=$(git rev-parse --show-toplevel)
exec python3 "$root/tools/security/scan_secrets.py" --staged --paths
"""

#: The second hook. A commit can pass with a clean index and the push can still
#: carry a credential that was committed earlier, so the push checks what is
#: already in the history rather than what is staged.
#:
#: Neither hook forwards its arguments. A hook is called with the refs it is
#: about, and this scanner answers a question about the tree rather than about a
#: ref, so a forwarded ref name is not a flag it knows and would be refused.
PRE_PUSH_TEMPLATE = """#!/bin/sh
# Installed by tools/security/scan_secrets.py --install-hook.
#
# A commit only sees the index, so a credential committed before this hook
# existed would pass it and leave with the push. This reads everything that is
# committed, which is what the remote would receive.
set -e
root=$(git rev-parse --show-toplevel)
exec python3 "$root/tools/security/scan_secrets.py" --tracked --paths
"""


def install_hook(root: Path) -> list[Path]:
    """Write both hooks. Returns the paths written, in the order they run."""
    written: list[Path] = []
    for name, template in (("pre-commit", HOOK_TEMPLATE), ("pre-push", PRE_PUSH_TEMPLATE)):
        hook = root / ".git" / "hooks" / name
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text(template, encoding="utf-8")
        hook.chmod(0o755)
        written.append(hook)
    return written


def _report(findings: list[Finding], mode: str, stream) -> None:
    secrets = [f for f in findings if f.kind == "secret"]
    paths = [f for f in findings if f.kind == "path"]

    if not findings:
        print(f"scan_secrets: {mode} clean, no credential and no closed path", file=stream)
        return

    print(
        f"scan_secrets: {mode} refused, {len(secrets)} credential(s) and "
        f"{len(paths)} closed path(s)",
        file=stream,
    )
    for finding in findings:
        print("  " + finding.render(), file=stream)
    if secrets:
        print(
            "  A matched value is masked. Rotate the credential before removing the "
            "line: it is already in the working tree and possibly in history.",
            file=stream,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scan_secrets",
        description="Refuse credentials and closed directories before they are pushed.",
    )
    view = parser.add_mutually_exclusive_group()
    view.add_argument("--staged", action="store_true", help="scan the index (default)")
    view.add_argument("--tracked", action="store_true", help="scan everything committed")
    view.add_argument("--all", action="store_true", help="scan tracked plus untracked, minus ignored")
    parser.add_argument(
        "--paths",
        action="store_true",
        help="also refuse the directories that are local by design",
    )
    parser.add_argument(
        "--install-hook",
        action="store_true",
        help="write .git/hooks/pre-commit and .git/hooks/pre-push",
    )
    parser.add_argument("--json", metavar="FILE", help="write the findings to a file as JSON")
    parser.add_argument("--quiet", action="store_true", help="report only the exit status")
    args = parser.parse_args(argv)

    try:
        root = repository_root()
    except RuntimeError as error:
        print(f"scan_secrets: {error}", file=sys.stderr)
        return 2

    if args.install_hook:
        for hook in install_hook(root):
            print(f"scan_secrets: installed {hook}")
        return 0

    mode = "tracked" if args.tracked else "all" if args.all else "staged"

    try:
        findings = scan_repository(root, mode)
    except (RuntimeError, ValueError) as error:
        print(f"scan_secrets: {error}", file=sys.stderr)
        return 2

    if args.paths:
        try:
            candidates = (
                _staged_paths(root)
                if mode == "staged"
                else _tracked_paths(root)
                if mode == "tracked"
                else _worktree_paths(root)
            )
        except RuntimeError as error:
            print(f"scan_secrets: {error}", file=sys.stderr)
            return 2
        findings.extend(check_paths(candidates))

    if not args.quiet:
        _report(findings, mode, sys.stdout)

    if args.json:
        destination = Path(args.json)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(
                {
                    "schema_version": "1",
                    "document": "secret-scan",
                    "mode": mode,
                    "clean": not findings,
                    "counts": {
                        "secret": sum(1 for f in findings if f.kind == "secret"),
                        "path": sum(1 for f in findings if f.kind == "path"),
                    },
                    "findings": [asdict(f) for f in findings],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
