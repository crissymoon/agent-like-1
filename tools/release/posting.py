"""The two sends: the private repository, and the copy that leaves the machine.

Both take the same reading and then push, and both are written here because they
are the same act with different preconditions. The act itself is one function,
so a change to how a send is leashed, recorded or reported cannot land in one of
them and not the other.

What is refused is what differs, and it is refused as a check rather than as a
guard beside the checks:

    the private send   adds to the private remote rather than rewriting it, and
                       is not addressed to the public copy
    the public send    is a commit the private remote already holds, and is not
                       addressed to the private repository

A refusal is a check that did not pass, so a run reports one list and one exit
status, and the receipt holds the same list whether anything was sent or not.
The receipt is written before a refusal is reported, because an attempt that
stopped is part of the record of what was read.

A send that could not ask its questions raises `ReleaseError` rather than
refusing, and that is the difference between a review that failed and a review
that never happened.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from checks import (  # noqa: E402
    Review,
    fast_forward_check,
    not_the_private_copy_check,
    not_the_public_copy_check,
    private_copy_is_ahead_check,
    review,
    write_receipt,
)
from git_ops import (  # noqa: E402
    destination_url,
    push_arguments,
    push_text,
    remote_branch,
)


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    """The arguments both sends take.

    The two remote names are the caller's, not this file's: a checkout decides
    what its remotes are called, and a review that reads the names rather than
    the URLs would have to be edited when a repository is renamed.
    """
    parser.add_argument("--branch", default="main", help="the branch being sent")
    parser.add_argument(
        "--receipt",
        default=None,
        help="where the reading is written, relative to the checkout unless absolute",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="send a rewritten history, leased against the object the remote holds",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="review and report the push that would run, without sending anything",
    )


def _relative(root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(root))
    except ValueError:
        return str(path)


def _render(report: Review, out) -> None:
    for line in report.render():
        out(line)


def _refuse(root: Path, report: Review, receipt: Path, out, reason: str) -> int:
    """Write the reading down anyway, so the attempt is on the record."""
    written = write_receipt(receipt, report)
    out(f"release: {reason}, the receipt is {_relative(root, written)}")
    return 1


def _send(
    root: Path,
    report: Review,
    *,
    destination: str,
    url: str,
    branch: str,
    force: bool,
    dry_run: bool,
    receipt: Path,
    out,
) -> int:
    """Push the reviewed commit, and record that it was pushed."""
    lease = remote_branch(root, destination, branch)
    arguments = push_arguments(destination, branch, lease, force)

    if dry_run:
        out("release: dry run, the command would be " + push_text(destination, branch, lease, force))
        written = write_receipt(receipt, report)
        out(f"release: nothing was sent, the receipt is {_relative(root, written)}")
        return 0

    out(f"release: sending {report.commit[:12]} to {destination} ({url}) as {branch}")
    # Run rather than capture: a push reports its progress over time and can ask
    # the credential helper a question, and both belong on the terminal.
    result = subprocess.run(arguments, cwd=str(root), check=False)
    if result.returncode != 0:
        return _refuse(root, report, receipt, out, "the push failed")

    written = write_receipt(receipt, report, destination=url)
    out(f"release: sent, the receipt is {_relative(root, written)}")
    return 0


def post_private(
    root: Path,
    *,
    branch: str = "main",
    remote: str = "origin",
    public_remote: str = "public",
    force: bool = False,
    dry_run: bool = False,
    receipt: Path,
    out=print,
) -> int:
    """Review the checkout, then send it to the private repository."""
    url = destination_url(root, remote)
    report = review(
        root,
        branch=branch,
        operation="private",
        extra_checks=(
            not_the_public_copy_check(root, remote, public_remote),
            fast_forward_check(root, branch, remote, force),
        ),
    )
    _render(report, out)
    if not report.ok:
        return _refuse(
            root, report, receipt, out, "the review did not come back clean, nothing was sent"
        )

    return _send(
        root,
        report,
        destination=remote,
        url=url,
        branch=branch,
        force=force,
        dry_run=dry_run,
        receipt=receipt,
        out=out,
    )


def post_public(
    root: Path,
    *,
    branch: str = "main",
    remote: str = "public",
    private_remote: str = "origin",
    force: bool = False,
    dry_run: bool = False,
    receipt: Path,
    out=print,
) -> int:
    """Review the checkout, then send it to the copy that leaves the machine."""
    url = destination_url(root, remote)
    report = review(
        root,
        branch=branch,
        operation="public",
        extra_checks=(
            private_copy_is_ahead_check(root, branch, private_remote),
            not_the_private_copy_check(root, remote, private_remote),
        ),
    )
    _render(report, out)
    if not report.ok:
        return _refuse(
            root, report, receipt, out, "the review did not come back clean, nothing was sent"
        )

    return _send(
        root,
        report,
        destination=remote,
        url=url,
        branch=branch,
        force=force,
        dry_run=dry_run,
        receipt=receipt,
        out=out,
    )


__all__ = ["add_common_arguments", "post_private", "post_public"]
