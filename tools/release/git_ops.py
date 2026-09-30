"""Reaching a remote, and wording the push that goes to it.

Nothing here decides anything. Which URL a remote name stands for, where its
branch is, and how a push is written so that a forced update carries a lease:
these are the questions both send commands ask, and they are asked here once
rather than written twice and answered differently the second time.

Standard input is closed on every call, because a git command that decides to
ask for a credential would otherwise wait for an answer that is never coming,
and a procedure that hangs reads as a procedure that is still running.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


class ReleaseError(RuntimeError):
    """A command a send depends on could not be run at all.

    Raised rather than returned, because a command that could not run answers
    nothing, and an empty answer is not the same as "nothing there". Reading the
    two as one is how a check that never ran passes.
    """


def repository_root() -> Path:
    """The working tree this is running inside, as git resolves it."""
    result = run_command(Path.cwd(), ["git", "rev-parse", "--show-toplevel"])
    if result.returncode != 0:
        raise ReleaseError("not inside a git working tree")

    return Path(result.stdout.decode("utf-8", errors="replace").strip())


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
    """A command's standard output and error, as one string."""
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


def excerpt(text: str, lines: int = 3) -> str:
    """The first few lines of a command's own output, joined onto one line."""
    stripped = [line.strip() for line in text.splitlines() if line.strip()]
    return " / ".join(stripped[:lines]) if stripped else "no output"


def git(root: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    """Run a git command whose exit status 1 is a verdict rather than a failure.

    `merge-base --is-ancestor` answers with its status, and so does `check-ignore`
    under `-q`, so a status of one is read here and by the caller. Anything else
    is a command that could not run.
    """
    result = run_command(root, ["git", *arguments])
    if result.returncode not in (0, 1):
        raise ReleaseError(
            f"git {arguments[0]} could not run: "
            f"{first_line(command_text(result)) or 'no output'}"
        )
    return result


def remote_url(root: Path, remote: str) -> str:
    """The URL a remote name stands for, or the empty string when it is not one."""
    result = run_command(root, ["git", "remote", "get-url", remote])
    if result.returncode != 0:
        return ""
    return first_line(command_text(result))


def destination_url(root: Path, remote: str) -> str:
    """The URL a send goes to, or a refusal naming the command that adds it."""
    url = remote_url(root, remote)
    if not url:
        raise ReleaseError(
            f"{remote} is not a remote of this checkout, so there is nowhere to send; "
            f"add it with `git remote add {remote} <url>`"
        )
    return url


def remote_branch(root: Path, remote: str, branch: str) -> str | None:
    """The object a remote's branch is at, or None when it has no such branch yet.

    Asked of the remote rather than of the local remote-tracking ref, so the
    answer is what the remote holds now instead of what it held the last time
    this checkout fetched.
    """
    result = run_command(root, ["git", "ls-remote", "--heads", remote, f"refs/heads/{branch}"])
    if result.returncode != 0:
        raise ReleaseError(
            f"{remote} could not be reached: {first_line(command_text(result)) or 'no output'}"
        )

    line = first_line(command_text(result))
    return line.split()[0] if line else None


def push_arguments(remote: str, branch: str, lease: str | None, force: bool) -> list[str]:
    """The push, with the lease a forced update has to carry.

    A remote branch that does not exist yet needs no lease: there is nothing to
    overwrite, and a plain push creates it. Where one does exist, the lease is
    the object the remote held when the review ran, which is the only thing
    between a forced update and somebody else's commit.
    """
    arguments = ["git", "push"]
    if force and lease:
        arguments.append(f"--force-with-lease=refs/heads/{branch}:{lease}")
    arguments.extend([remote, f"HEAD:refs/heads/{branch}"])
    return arguments


def push_text(remote: str, branch: str, lease: str | None, force: bool) -> str:
    """The push above, as the line a dry run prints."""
    return " ".join(push_arguments(remote, branch, lease, force))


__all__ = [
    "ReleaseError",
    "command_text",
    "destination_url",
    "excerpt",
    "first_line",
    "git",
    "last_line",
    "push_arguments",
    "push_text",
    "remote_branch",
    "remote_url",
    "repository_root",
    "run_command",
]
