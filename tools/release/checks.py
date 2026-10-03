"""The review a checkout passes before either copy is sent to.

Ten questions are asked of the commit every time, and each of the two send
commands adds the two preconditions that belong to it. The ten are the ones
that hold wherever the commit is going: whether anything that belongs on this
machine is in the tree, whether the reading is of the commit a person is looking
at, whether a page the repository holds is readable where it is read, and
whether every file in it is written down with its size and the hash of its
content so the list can be read and compared.

The two commands differ in what they refuse rather than in what they read. A
send to the private repository must add to it rather than rewrite it, and must
not be the public one; a send to the public copy must be a commit the private
one already holds, and must not be the private one. Those four are checks too,
so both commands report one list and one exit status rather than a list with a
refusal bolted on beside it.

The review reads the object database rather than the working tree wherever it
can, because the question is what a push would send. The cleanliness check is
the one place that looks at the working tree, and it looks so that it can prove
the two agree.

Every check returns its verdict rather than raising, so one review reports the
whole list instead of stopping at the first refusal. A command that cannot run
at all is the exception: that raises `ReleaseError`, because a review that could
not be taken is not a review that passed.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent

if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
if str(TOOLS / "security") not in sys.path:
    sys.path.insert(0, str(TOOLS / "security"))

from git_ops import (  # noqa: E402
    ReleaseError,
    command_text,
    excerpt,
    first_line,
    git,
    last_line,
    remote_branch,
    remote_url,
    run_command,
)

# The closed directory list is the scanner's, not a second copy of it. Two lists
# of the same thing is one list that drifts, and the one that drifts is always
# the one nobody is running.
from scan_secrets import FORBIDDEN_PATHS  # noqa: E402

#: The directory the operating procedures live in, relative to the root. The
#: name is held out by `.gitignore` and refused by name by the scanner; it is
#: stated here so the receipts have somewhere to go that cannot be committed by
#: accident.
SOP_DIRECTORY = "Standard Operation Procedures"

#: The receipt each command writes. Two names rather than one, because the two
#: records answer different questions: what went to the private repository, and
#: what went to the copy that leaves the machine. One name would mean the second
#: send erased the record of the first.
PRIVATE_RECEIPT_NAME = "private-post.json"
PUBLIC_RECEIPT_NAME = "public-post.json"

#: A committed file larger than this is a mistake rather than an artefact. The
#: weights, the Electron runtime and the archives are held out of the tree, so
#: what is left is source, a figure, or a fixture.
MAX_TRACKED_FILE_BYTES = 8 * 1024 * 1024

#: How many items a failing detail lists before it stops counting. The count is
#: always in the line ahead of it, so nothing is lost by stopping.
EXCERPT_ITEMS = 3


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
    operation: str
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
            f"    {self.operation} send of {self.commit[:12]} on {self.branch}, "
            f"{len(self.entries)} file(s), {size_label(self.total_bytes)}",
        ]
        lines.extend(check.render() for check in self.checks)
        return lines

    def as_document(self, destination: str | None = None) -> dict:
        """The receipt: the reading as data, so it can be kept and compared.

        `destination` is the URL a send went to, and it is null for a reading
        that stopped at reading.
        """
        return {
            "schema_version": "1",
            "document": "release-review",
            "operation": self.operation,
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
        raise ReleaseError("git cat-file could not run: " + first_line(command_text(result)))

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
    listing = git(root, "ls-tree", "-r", "-z", "--long", "HEAD")

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
# The questions asked of every commit, wherever it is going.
# ---------------------------------------------------------------------------


def working_tree_check(root: Path) -> Check:
    """The commit being read is the commit a person is looking at.

    An edit that is not committed, a file staged and then changed, a stray file
    nobody meant to keep: each of them means the checkout and the commit have
    come apart, and a review of one while looking at the other is not a review.
    """
    name = "the checkout holds the commit and nothing else"
    lines = [
        line
        for line in command_text(git(root, "status", "--porcelain")).splitlines()
        if line.strip()
    ]
    if not lines:
        return Check(name, True, "git status is empty")

    return Check(name, False, f"{len(lines)} path(s) differ from the commit: {excerpt(chr(10).join(lines))}")


def branch_check(root: Path, branch: str) -> Check:
    """A send names one branch, and this is the checkout that is on it."""
    name = "the checkout is on the branch being sent"
    current = first_line(command_text(git(root, "rev-parse", "--abbrev-ref", "HEAD"))) or "(detached)"
    if current == branch:
        return Check(name, True, f"HEAD is {branch}")

    return Check(name, False, f"HEAD is {current}, and the send names {branch}")


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
        raise ReleaseError("the secret scanner could not run: " + (verdict or "no output"))

    return Check(name, result.returncode == 0, verdict or "no output")


def security_scan_check(root: Path) -> Check:
    """The whole security reading, at the level the review refuses.

    The secret scanner is one of the four surfaces this reads, so the two checks
    beside this one ask a narrower question and this asks the whole one: source
    constructs, file modes and names, and the dependency set, in addition to what
    a credential looks like. It runs the same command a person runs, so a review
    and a manual reading cannot disagree about what is clean.
    """
    name = "the security reading is clean at the level a push refuses"
    result = run_command(
        root,
        [
            sys.executable,
            str(TOOLS / "security" / "scan_all.py"),
            "--fail-on",
            "high",
            "--quiet",
        ],
    )
    verdict = first_line(command_text(result))
    if result.returncode == 2:
        raise ReleaseError("the security scan could not run: " + (verdict or "no output"))

    return Check(name, result.returncode == 0, verdict or "no output")


def recorded_path_check(root: Path) -> Check:
    """A recorded run names the repository, a home marker or the temporary one."""
    name = "every recorded path is repository relative"
    result = run_command(root, [sys.executable, str(TOOLS / "normalize_paths.py"), "--check"])
    verdict = first_line(command_text(result))
    if result.returncode == 2:
        raise ReleaseError("the path shortener could not run: " + (verdict or "no output"))
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
        raise ReleaseError("the README builder could not run: " + (verdict or "no output"))

    return Check(name, result.returncode == 0, verdict or "no output")


def page_markdown_check(root: Path) -> Check:
    """The markdown of an exported page is addressed for the reader that finds it.

    A page is two files with two readers. The page beside its images is served
    from the folder it sits in, and the markdown of the same name is rendered by
    GitHub, which resolves every path from the tree instead. A reference written
    for the server - an image from the site root, a link to the exported `.html`
    - answers to the first reader and to nothing for the second, and it reaches
    the repository as a broken image or a source listing rather than a page.

    The export hands the copy to `github_md.py`, so the same tool is asked here
    rather than a second reading of the same rule. It is asked of the commit
    because a page exported before the converter ran, or exported to a
    destination that has none, carries the server's addressing into the
    repository otherwise, and a page that is broken on the remote is read by a
    person before it is fixed.
    """
    name = "every exported page's markdown is addressed for GitHub"
    result = run_command(root, [sys.executable, str(TOOLS / "github_md.py"), "--check"])
    verdict = last_line(command_text(result))
    if result.returncode not in (0, 1):
        raise ReleaseError("the markdown converter could not run: " + (verdict or "no output"))

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
        listed = first_line(command_text(git(root, "ls-files", "--", rule.prefix)))
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
        + ", ".join(f"{entry.path} at {size_label(entry.size)}" for entry in over[:EXCERPT_ITEMS]),
    )


# ---------------------------------------------------------------------------
# The preconditions that belong to one of the two sends.
# ---------------------------------------------------------------------------


def _distinct(
    root: Path, destination: str, other: str, name: str, consequence: str
) -> Check:
    """Two remotes, resolved by URL rather than trusted by name.

    The name is the thing that was wrong when this goes wrong, so the two names
    are resolved and compared instead of the command being asked whether it
    looks right.
    """
    here = remote_url(root, destination)
    there = remote_url(root, other)
    if not there:
        return Check(
            name,
            True,
            f"{other} is not a remote of this checkout, so there is nothing to be mistaken for it",
        )
    if here != there:
        return Check(name, True, f"{destination} is {here}, and {other} is not")

    return Check(name, False, f"{destination} and {other} are both {here}, so {consequence}")


def not_the_public_copy_check(root: Path, destination: str, public_remote: str) -> Check:
    """A send to the private repository that went to the public one instead.

    That mistake has no symptom of its own: the push succeeds, the files land
    where the reviewed commit already was, and the private checkout's records
    have left the machine without the preconditions the public send applies.
    """
    return _distinct(
        root,
        destination,
        public_remote,
        "the destination is not the public repository",
        "the private checkout would have gone to the public copy",
    )


def not_the_private_copy_check(root: Path, destination: str, private_remote: str) -> Check:
    """A send to the public copy that went to the private repository instead.

    The same mistake in the other direction, and the one the procedure exists
    for. A push to the private repository is not a publication: it looks exactly
    like a publication that worked, and the public copy is left behind.
    """
    return _distinct(
        root,
        destination,
        private_remote,
        "the destination is not the private repository",
        "the public copy would not have been written to",
    )


def private_copy_is_ahead_check(root: Path, branch: str, remote: str) -> Check:
    """The commit is already in the private remote, so the order of the two holds.

    A commit that reached the public copy before it reached the private one is a
    commit the private history does not hold, and the next rewrite of either
    would have to reconcile the two by hand.
    """
    name = "the commit is already in the private remote"
    remote_branch_name = f"{remote}/{branch}"
    head = first_line(command_text(git(root, "rev-parse", "--short", "HEAD"))) or "HEAD"

    result = run_command(root, ["git", "merge-base", "--is-ancestor", "HEAD", remote_branch_name])
    if result.returncode == 0:
        return Check(name, True, f"{head} is in {remote_branch_name}")
    if result.returncode == 1:
        return Check(
            name,
            False,
            f"{head} is not in {remote_branch_name}; send it to {remote} first",
        )

    return Check(name, False, f"{remote_branch_name} does not exist; fetch {remote} first")


def fast_forward_check(root: Path, branch: str, remote: str, force: bool) -> Check:
    """What the push does to the remote branch: adds to it, or was asked to rewrite it.

    The remote's branch is read from the remote rather than from the local
    remote-tracking ref, so this is the state the push will meet rather than the
    state of the last fetch. Where the branch holds something this commit does
    not continue, the check passes only when a forced update was asked for, and
    the push itself carries the lease that makes it deliberate.
    """
    name = "the push adds to the remote branch rather than rewriting it"
    target = remote_branch(root, remote, branch)
    if target is None:
        return Check(name, True, f"{remote} has no {branch} yet, so the push creates it")

    result = run_command(root, ["git", "merge-base", "--is-ancestor", target, "HEAD"])
    if result.returncode == 0:
        return Check(name, True, f"{remote}/{branch} is behind HEAD, so the push is a fast forward")
    if result.returncode != 1:
        return Check(
            name,
            False,
            f"{target[:12]} is not in this checkout, so it cannot be compared; fetch {remote} first",
        )
    if force:
        return Check(
            name,
            True,
            f"{remote}/{branch} holds {target[:12]}, which this commit does not continue, "
            "and a forced update was asked for, leased against that object",
        )

    return Check(
        name,
        False,
        f"{remote}/{branch} holds {target[:12]}, which this commit does not continue; "
        "--force sends a lease against it, and sending nothing is the other answer",
    )


# ---------------------------------------------------------------------------
# The whole reading.
# ---------------------------------------------------------------------------


def review(
    root: Path,
    *,
    branch: str = "main",
    operation: str = "public",
    extra_checks: tuple[Check, ...] = (),
) -> Review:
    """The whole reading: the ten shared questions, then the two that belong here."""
    entries = manifest(root)
    commit = first_line(command_text(git(root, "rev-parse", "HEAD")))
    if not commit:
        raise ReleaseError("HEAD could not be resolved, so there is nothing to review")

    return Review(
        commit=commit,
        branch=branch,
        operation=operation,
        checks=(
            working_tree_check(root),
            branch_check(root, branch),
            secret_scan_check(root, "tracked"),
            secret_scan_check(root, "history"),
            security_scan_check(root),
            recorded_path_check(root),
            readme_check(root),
            page_markdown_check(root),
            self_check(root),
            closed_directory_check(root),
            largest_file_check(entries),
        )
        + extra_checks,
        entries=entries,
    )


# ---------------------------------------------------------------------------
# The receipt.
# ---------------------------------------------------------------------------


def resolve_receipt(root: Path, override: str | None, name: str) -> Path:
    """Where a reading is written: the name given, or the one the command owns.

    A relative override is taken against the checkout rather than the process, so
    the same command writes to the same place from anywhere.
    """
    if not override:
        return root / SOP_DIRECTORY / name
    path = Path(override).expanduser()
    return path if path.is_absolute() else root / path


def write_receipt(target: Path, report: Review, destination: str | None = None) -> Path:
    """Write the reading down, in the one directory that is not version controlled.

    The directory is created on the first review rather than committed, so a
    checkout that has never sent anything does not have one and does not need
    one.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(report.as_document(destination), indent=2) + "\n", encoding="utf-8"
    )
    return target
