"""The review a private checkout passes before any of it is published.

Two different questions are being answered, and both have to be answered before
a commit is published. The first is whether anything that belongs on this
machine is in the tree: a credential, a machine path in a recorded run, a
directory that exists for local work added by name. The second is whether what
would be published is whole and is what was read: the checkout holds no
uncommitted edit, the branch is the one the private remote already has, and
every file in the commit is written down with its size and the hash of its
content so the list can be read and compared.

The review reads the object database rather than the working tree wherever it
can, because the question is what a push would send. The cleanliness check is
the one place that looks at the working tree, and it looks so that it can prove
the two agree.

Every check returns its verdict rather than raising, so one review reports the
whole list instead of stopping at the first refusal. A command that cannot run
at all is the exception: that raises `ReviewError`, because a review that could
not be taken is not a review that passed.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent
PROJECT = TOOLS.parent

if str(TOOLS / "security") not in sys.path:
    sys.path.insert(0, str(TOOLS / "security"))

# The closed directory list is the scanner's, not a second copy of it. Two
# lists of the same thing is one list that drifts, and the one that drifts is
# always the one nobody is running.
from scan_secrets import FORBIDDEN_PATHS  # noqa: E402

#: The directory the operating procedures live in, relative to the root. The
#: name is held out by `.gitignore` and refused by name by the scanner; it is
#: stated here so the receipt has somewhere to go that cannot be committed by
#: accident.
SOP_DIRECTORY = "Standard Operation Procedures"

#: The receipt a review writes, inside the directory above.
RECEIPT_NAME = "release-review.json"

#: A committed file larger than this is a mistake rather than an artefact. The
#: weights, the Electron runtime and the archives are held out of the tree, so
#: what is left is source, a figure, or a fixture.
MAX_TRACKED_FILE_BYTES = 8 * 1024 * 1024

#: How much of a command's own output a report keeps. A scanner prints its
#: verdict on the first line and the findings under it, and the verdict is what
#: a reader of the list wants; the rest is on the terminal when it is run alone.
EXCERPT_LINES = 3


class ReviewError(RuntimeError):
    """A command the review depends on could not be run at all."""


@dataclass(frozen=True)
class Check:
    """One claim the review makes, and what it found when it made it."""

    name: str
    ok: bool
    detail: str

    def render(self) -> str:
        mark = "ok    " if self.ok else "FAILED"
        return f"{mark}  {self.name}\n          {self.detail}"


@dataclass(frozen=True)
class Entry:
    """One file the commit holds: where it sits, how big it is, what it hashes to."""

    path: str
    size: int
    object_id: str
    sha256: str


@dataclass(frozen=True)
class Review:
    """The whole reading: the commit, the claims about it, and its file list."""

    commit: str
    branch: str
    checks: tuple[Check, ...]
    entries: tuple[Entry, ...]

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)

    @property
    def failures(self) -> tuple[Check, ...]:
        return tuple(check for check in self.checks if not check.ok)

    @property
    def total_bytes(self) -> int:
        return sum(entry.size for entry in self.entries)

    def render(self) -> list[str]:
        lines = [
            f"release review: {len(self.checks)} check(s), {len(self.failures)} failed",
            f"    commit {self.commit[:12]} on {self.branch}, "
            f"{len(self.entries)} file(s), {size_label(self.total_bytes)}",
        ]
        lines.extend(check.render() for check in self.checks)
        return lines

    def as_document(self, destination: str | None = None) -> dict:
        """The receipt: the reading as data, so it can be kept and compared.

        `destination` is the URL a publication was sent to, and it is null for a
        review that stopped at reading.
        """
        return {
            "schema_version": "1",
            "document": "release-review",
            "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "branch": self.branch,
            "commit": self.commit,
            "clean": self.ok,
            "destination": destination,
            "counts": {
                "checks": len(self.checks),
                "failed": len(self.failures),
                "files": len(self.entries),
                "bytes": self.total_bytes,
            },
            "checks": [
                {"name": check.name, "ok": check.ok, "detail": check.detail}
                for check in self.checks
            ],
            "files": [
                {
                    "path": entry.path,
                    "bytes": entry.size,
                    "object": entry.object_id,
                    "sha256": entry.sha256,
                }
                for entry in self.entries
            ],
        }


def size_label(value: int) -> str:
    """A byte count as a reader would say it."""
    if value >= 1024**3:
        return f"{value / 1024**3:.2f} GB"
    if value >= 1024**2:
        return f"{value / 1024**2:.1f} MB"
    if value >= 1024:
        return f"{value / 1024:.1f} KB"
    return f"{value} B"


def run_command(
    root: Path, argv: list[str], data: bytes | None = None
) -> subprocess.CompletedProcess[bytes]:
    """Run a command in the checkout and capture it.

    Standard input is closed when nothing is being fed to the command, because a
    git command that decides to ask for a credential would otherwise wait for an
    answer that is never coming.
    """
    return subprocess.run(
        argv,
        cwd=str(root),
        capture_output=True,
        input=data,
        stdin=None if data is not None else subprocess.DEVNULL,
        check=False,
    )


def command_text(result: subprocess.CompletedProcess[bytes]) -> str:
    """A command's standard output and error, worst first, as one string."""
    parts = [
        result.stdout.decode("utf-8", errors="replace"),
        result.stderr.decode("utf-8", errors="replace"),
    ]
    return "\n".join(part for part in parts if part.strip())


def first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def last_line(text: str) -> str:
    stripped = [line.strip() for line in text.splitlines() if line.strip()]
    return stripped[-1] if stripped else ""


def _excerpt(text: str) -> str:
    stripped = [line.strip() for line in text.splitlines() if line.strip()]
    return " / ".join(stripped[:EXCERPT_LINES]) if stripped else "no output"


def _git(root: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    result = run_command(root, ["git", *arguments])
    if result.returncode not in (0, 1):
        raise ReviewError(
            f"git {arguments[0]} could not run: "
            f"{first_line(command_text(result)) or 'no output'}"
        )
    return result


# ---------------------------------------------------------------------------
# The commit, read from the object database.
# ---------------------------------------------------------------------------


def _blob_contents(root: Path, object_ids: list[str]) -> dict[str, bytes]:
    """The content of several blobs, read in one pass over the object database."""
    unique = list(dict.fromkeys(object_ids))
    if not unique:
        return {}

    request = "".join(f"{object_id}\n" for object_id in unique).encode("ascii")
    result = run_command(root, ["git", "cat-file", "--batch"], data=request)
    if result.returncode != 0:
        raise ReviewError("git cat-file could not run: " + first_line(command_text(result)))

    data = result.stdout
    contents: dict[str, bytes] = {}
    position = 0
    for object_id in unique:
        end = data.find(b"\n", position)
        if end < 0:
            break
        header = data[position:end].decode("utf-8", errors="replace").split()
        position = end + 1
        if len(header) < 3 or "missing" in header:
            continue
        size = int(header[2])
        contents[object_id] = data[position : position + size]
        position += size + 1

    return contents


def manifest(root: Path) -> tuple[Entry, ...]:
    """Every file at HEAD, with its size and the hash of what it holds.

    The listing and the contents both come from the object database rather than
    from the working tree, because the question this answers is what a push
    would send. A file edited on disk and not committed is not part of the
    answer; the cleanliness check is what proves the two agree.
    """
    listing = _git(root, "ls-tree", "-r", "-z", "--long", "HEAD")

    records: list[tuple[str, int, str]] = []
    for record in listing.stdout.decode("utf-8", errors="replace").split("\0"):
        if not record:
            continue
        header, separator, path = record.partition("\t")
        if not separator:
            continue
        fields = header.split()
        if len(fields) != 4 or fields[1] != "blob":
            continue
        records.append((path, int(fields[3]), fields[2]))

    contents = _blob_contents(root, [object_id for _, _, object_id in records])
    return tuple(
        Entry(
            path=path,
            size=size,
            object_id=object_id,
            sha256=hashlib.sha256(contents.get(object_id, b"")).hexdigest(),
        )
        for path, size, object_id in records
    )


# ---------------------------------------------------------------------------
# The checks.
# ---------------------------------------------------------------------------


def working_tree_check(root: Path) -> Check:
    """The commit being read is the commit a person is looking at.

    An edit that is not committed, a file staged and then changed, a stray file
    nobody meant to keep: each of them means the checkout and the commit have
    come apart, and a review of one while looking at the other is not a review.
    """
    name = "the checkout holds the commit and nothing else"
    lines = [line for line in command_text(_git(root, "status", "--porcelain")).splitlines() if line.strip()]
    if not lines:
        return Check(name, True, "git status is empty")

    return Check(name, False, f"{len(lines)} path(s) differ from the commit: {_excerpt(chr(10).join(lines))}")


def branch_check(root: Path, branch: str) -> Check:
    """Publication names one branch, and this is the checkout that is on it."""
    name = "the checkout is on the branch being published"
    current = first_line(command_text(_git(root, "rev-parse", "--abbrev-ref", "HEAD"))) or "(detached)"
    if current == branch:
        return Check(name, True, f"HEAD is {branch}")

    return Check(name, False, f"HEAD is {current}, and the review publishes {branch}")


def private_remote_check(root: Path, branch: str, remote: str) -> Check:
    """The commit is already in the private remote, so the order of the two holds.

    A commit that reached the public copy before it reached the private one is a
    commit the private history does not hold, and the next rewrite of either
    would have to reconcile the two by hand.
    """
    name = "the commit is already in the private remote"
    remote_branch = f"{remote}/{branch}"
    head = first_line(command_text(_git(root, "rev-parse", "--short", "HEAD"))) or "HEAD"

    result = run_command(root, ["git", "merge-base", "--is-ancestor", "HEAD", remote_branch])
    if result.returncode == 0:
        return Check(name, True, f"{head} is in {remote_branch}")
    if result.returncode == 1:
        return Check(
            name,
            False,
            f"{head} is not in {remote_branch}; push it to {remote} before publishing",
        )

    return Check(name, False, f"{remote_branch} does not exist; fetch {remote} first")


def secret_scan_check(root: Path, mode: str) -> Check:
    """One view of the scanner, run the way the hook runs it.

    The scanner decides what a credential, a closed path and a machine path are,
    so the review asks it rather than asking a second implementation.
    """
    name = (
        "the tracked tree holds no credential, no closed path and no machine path"
        if mode == "tracked"
        else "the history holds no credential, no closed path and no machine path"
    )
    result = run_command(
        root,
        [sys.executable, str(TOOLS / "security" / "scan_secrets.py"), f"--{mode}", "--paths"],
    )
    verdict = first_line(command_text(result))
    if result.returncode == 2:
        raise ReviewError("the secret scanner could not run: " + (verdict or "no output"))

    return Check(name, result.returncode == 0, verdict or "no output")


def recorded_path_check(root: Path) -> Check:
    """A recorded run names the repository, a home marker or the temporary one."""
    name = "every recorded path is repository relative"
    result = run_command(root, [sys.executable, str(TOOLS / "normalize_paths.py"), "--check"])
    verdict = first_line(command_text(result))
    if result.returncode == 2:
        raise ReviewError("the path shortener could not run: " + (verdict or "no output"))
    if result.returncode == 0:
        return Check(name, True, verdict or "nothing to shorten")

    return Check(
        name,
        False,
        (verdict or "something would change") + "; run tools/normalize_paths.py --write",
    )


def readme_check(root: Path) -> Check:
    """The document a reader arrives at matches the artefacts it is built from."""
    name = "the README matches the artefacts it is built from"
    result = run_command(root, [sys.executable, str(TOOLS / "build_readme.py"), "--check"])
    verdict = first_line(command_text(result))
    if result.returncode not in (0, 1):
        raise ReviewError("the README builder could not run: " + (verdict or "no output"))

    return Check(name, result.returncode == 0, verdict or "no output")


def self_check(root: Path) -> Check:
    """The harness checks itself on this machine, and the review reads the result."""
    name = "the self-check passes"
    php = shutil.which("php")
    if php is None:
        return Check(name, False, "php is not on PATH, so the harness cannot be checked")

    result = run_command(root, [php, str(root / "agent.php"), "--self-check"])
    verdict = last_line(command_text(result))
    return Check(name, result.returncode == 0, verdict or "no output")


def closed_directory_check(root: Path) -> Check:
    """Every directory that is local by design, and the rule that holds it out.

    The rule is checked rather than the directory: a path is refused by the
    ignore file whether or not it exists yet, which is what makes this hold for
    a checkout that has never run the tester or taken a review.
    """
    name = "every closed directory is held out of the tree"
    present: list[str] = []
    unheld: list[str] = []
    tracked: list[str] = []

    for rule in FORBIDDEN_PATHS:
        directory = rule.prefix.rstrip("/")
        if (root / directory).exists():
            present.append(directory)
        ignored = run_command(root, ["git", "check-ignore", "-q", rule.prefix]).returncode == 0
        if not ignored:
            unheld.append(directory)
        listed = first_line(command_text(_git(root, "ls-files", "--", rule.prefix)))
        if listed:
            tracked.append(directory)

    if not unheld and not tracked:
        return Check(
            name,
            True,
            f"{len(FORBIDDEN_PATHS)} rule(s) hold, {len(present)} of them on this machine",
        )

    parts = []
    if unheld:
        parts.append("not held out: " + ", ".join(unheld))
    if tracked:
        parts.append("committed anyway: " + ", ".join(tracked))
    return Check(name, False, "; ".join(parts))


def largest_file_check(entries: tuple[Entry, ...]) -> Check:
    """Nothing in the commit is a size a release does not carry."""
    name = "no tracked file is larger than a release carries"
    largest = max(entries, key=lambda entry: entry.size, default=None)
    if largest is None:
        return Check(name, False, "the commit holds no files at all")

    over = [entry for entry in entries if entry.size > MAX_TRACKED_FILE_BYTES]
    if not over:
        return Check(
            name,
            True,
            f"the largest is {largest.path} at {size_label(largest.size)}, "
            f"the limit is {size_label(MAX_TRACKED_FILE_BYTES)}",
        )

    return Check(
        name,
        False,
        f"{len(over)} file(s) over {size_label(MAX_TRACKED_FILE_BYTES)}: "
        + ", ".join(f"{entry.path} at {size_label(entry.size)}" for entry in over[:EXCERPT_LINES]),
    )


def review(root: Path, branch: str = "main", private_remote: str = "origin") -> Review:
    """The whole reading, in the order a reader would ask the questions."""
    entries = manifest(root)
    commit = first_line(command_text(_git(root, "rev-parse", "HEAD")))
    if not commit:
        raise ReviewError("HEAD could not be resolved, so there is nothing to review")

    return Review(
        commit=commit,
        branch=branch,
        checks=(
            working_tree_check(root),
            branch_check(root, branch),
            private_remote_check(root, branch, private_remote),
            secret_scan_check(root, "tracked"),
            secret_scan_check(root, "history"),
            recorded_path_check(root),
            readme_check(root),
            self_check(root),
            closed_directory_check(root),
            largest_file_check(entries),
        ),
        entries=entries,
    )


def receipt_path(root: Path, override: Path | None = None) -> Path:
    """Where the receipt goes: beside the procedure it is the record of."""
    return override if override is not None else root / SOP_DIRECTORY / RECEIPT_NAME


def write_receipt(
    root: Path,
    report: Review,
    destination: str | None = None,
    override: Path | None = None,
) -> Path:
    """Write the reading down in the one directory that is not version controlled.

    The directory is created on the first review rather than committed, so a
    checkout that has never published does not have one and does not need one.
    """
    target = receipt_path(root, override)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(report.as_document(destination), indent=2) + "\n", encoding="utf-8"
    )
    return target
