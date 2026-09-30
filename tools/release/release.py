"""The command line over the release review: read the checkout, then publish it.

    python3 tools/release/release.py review
    python3 tools/release/release.py publish

`review` answers the two questions a publication depends on: whether anything
that belongs on this machine is in the commit, and whether the commit is whole
and is the one the private remote already holds. It writes the reading down as
a receipt in `Standard Operation Procedures/`, the directory that is held out of
both repositories, so the record of what was reviewed is beside the procedure
that asked for it rather than in the tree.

`publish` takes the same reading, refuses to send anything when it is not clean,
and pushes the commit to the public remote. The review is not a step that can be
skipped: there is no flag that sends an unreviewed commit, because the flag
would be the one that gets used at the end of a long day.

Exit status is zero when the review is clean, one when it is not, and two when
the review could not be taken at all, so a checkout in a state where the checks
cannot run is refused rather than passed.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

try:  # the package
    from .checks import ReviewError, review, write_receipt
    from .publish import publish
except ImportError:  # run beside its own siblings
    from checks import ReviewError, review, write_receipt  # type: ignore[no-redef]
    from publish import publish  # type: ignore[no-redef]


def repository_root() -> Path:
    """The working tree this is running inside, as git resolves it."""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], capture_output=True, check=False
    )
    if result.returncode != 0:
        raise ReviewError("not inside a git working tree")

    return Path(result.stdout.decode("utf-8", errors="replace").strip())


def _receipt(path: Path | None, root: Path) -> Path | None:
    """A receipt path, taken as relative to the checkout when it is not absolute."""
    if path is None:
        return None
    return path if path.is_absolute() else root / path


def review_command(root: Path, args: argparse.Namespace) -> int:
    """Take the reading, write it down, and report it."""
    report = review(root, branch=args.branch, private_remote=args.private_remote)
    for line in report.render():
        print(line)

    written = write_receipt(root, report, override=_receipt(args.receipt, root))
    try:
        shown = written.resolve().relative_to(root)
    except ValueError:
        shown = written
    print(f"release: the receipt is {shown}")

    return 0 if report.ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="release",
        description="Review a private checkout, then publish the reviewed commit.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    review_parser = commands.add_parser(
        "review", help="read the checkout and write the receipt"
    )
    publish_parser = commands.add_parser(
        "publish", help="review the checkout, then send the commit to the public remote"
    )
    for sub in (review_parser, publish_parser):
        sub.add_argument("--branch", default="main", help="the branch being published")
        sub.add_argument(
            "--private-remote",
            default="origin",
            help="the remote that has to hold the commit already",
        )
        sub.add_argument(
            "--receipt",
            default=None,
            help="where to write the reading, default Standard Operation Procedures/release-review.json",
        )
    publish_parser.add_argument(
        "--public-remote", default="public", help="the remote the public copy lives in"
    )
    publish_parser.add_argument(
        "--force",
        action="store_true",
        help="send a rewritten history, leased against the object the remote holds",
    )
    publish_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="review and report the push that would run, without sending anything",
    )

    args = parser.parse_args(argv)

    try:
        root = repository_root()
        if args.command == "review":
            return review_command(root, args)

        return publish(
            root,
            branch=args.branch,
            private_remote=args.private_remote,
            public_remote=args.public_remote,
            force=args.force,
            dry_run=args.dry_run,
            receipt=_receipt(args.receipt, root),
        )
    except ReviewError as error:
        print(f"release: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
