"""Sending a reviewed commit to the public copy, and nothing else.

Publication is the one step of this procedure that a later commit cannot undo,
so it is the step with the least freedom in it. Two things are refused outright.

The first is a review that does not come back clean. This takes the reading
itself rather than trusting a receipt written earlier, because a receipt says
what was true when it was written and the question here is what is true now.

The second is publishing to the private remote. That mistake has no symptom:
the push succeeds, the files are where they already were, and the procedure
appears to have run.

Everything else about a publication is a git push, and is left to git. The
destination is a remote, the branch is a branch, and a push that is not a fast
forward fails unless a forced update was asked for. A forced update is offered
because a rewritten history cannot be published without one, and it is offered
as a lease against the object the remote was at when this ran, so a remote that
moved in between is refused rather than overwritten.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

try:  # the package
    from .checks import (
        Review,
        ReviewError,
        command_text,
        first_line,
        review,
        run_command,
        write_receipt,
    )
except ImportError:  # run beside its own sibling
    from checks import (  # type: ignore[no-redef]
        Review,
        ReviewError,
        command_text,
        first_line,
        review,
        run_command,
        write_receipt,
    )


def remote_url(root: Path, remote: str) -> str:
    """The URL a remote name stands for, or the empty string when it is not one."""
    result = run_command(root, ["git", "remote", "get-url", remote])
    if result.returncode != 0:
        return ""
    return first_line(command_text(result))


def destination_url(root: Path, remote: str) -> str:
    """The URL publication goes to, or a refusal naming the command that adds it."""
    url = remote_url(root, remote)
    if not url:
        raise ReviewError(
            f"{remote} is not a remote of this checkout, so there is nowhere to publish; "
            f"add it with `git remote add {remote} <url>`"
        )

    return url


def remote_branch(root: Path, remote: str, branch: str) -> str | None:
    """The object a remote's branch is at, or None when it has no such branch yet."""
    result = run_command(root, ["git", "ls-remote", "--heads", remote, f"refs/heads/{branch}"])
    if result.returncode != 0:
        raise ReviewError(
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


def _record(root: Path, report: Review, receipt: Path | None, destination: str | None) -> Path:
    return write_receipt(root, report, destination=destination, override=receipt)


def _refuse(root: Path, report: Review, receipt: Path | None, out, reason: str) -> int:
    """Write the reading down anyway, so the attempt is on the record."""
    written = _record(root, report, receipt, None)
    out(f"release: {reason}, the receipt is {_relative(root, written)}")
    return 1


def _relative(root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(root))
    except ValueError:
        return str(path)


def publish(
    root: Path,
    *,
    branch: str = "main",
    private_remote: str = "origin",
    public_remote: str = "public",
    force: bool = False,
    dry_run: bool = False,
    receipt: Path | None = None,
    out=print,
) -> int:
    """Review, then send the commit. Returns the exit status the command reports."""
    report = review(root, branch=branch, private_remote=private_remote)
    for line in report.render():
        out(line)

    if not report.ok:
        return _refuse(root, report, receipt, out, "the review did not come back clean, nothing was sent")

    url = destination_url(root, public_remote)
    if url == remote_url(root, private_remote):
        return _refuse(
            root,
            report,
            receipt,
            out,
            f"{public_remote} is the private remote, nothing was sent",
        )

    lease = remote_branch(root, public_remote, branch)
    arguments = push_arguments(public_remote, branch, lease, force)

    if dry_run:
        out("release: dry run, the command would be " + " ".join(arguments))
        written = _record(root, report, receipt, None)
        out(f"release: nothing was sent, the receipt is {_relative(root, written)}")
        return 0

    out(f"release: sending {report.commit[:12]} to {public_remote} ({url}) as {branch}")
    # Run rather than capture: a push reports its progress over time and can ask
    # the credential helper a question, and both belong on the terminal.
    result = subprocess.run(arguments, cwd=str(root), check=False)
    if result.returncode != 0:
        return _refuse(root, report, receipt, out, "the push failed")

    written = _record(root, report, receipt, url)
    out(f"release: published, the receipt is {_relative(root, written)}")
    return 0


__all__ = ["destination_url", "publish", "push_arguments", "remote_branch", "remote_url"]
