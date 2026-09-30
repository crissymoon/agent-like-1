"""Send this checkout to the private repository, once the review is clean.

    python3 tools/release/post_private.py --dry-run   # read, and report the push
    python3 tools/release/post_private.py             # read, then send

This is the first of the two sends and the one that runs most often. It is the
same reading as the public send, and it is here rather than left to a bare
`git push` for the reason the review exists at all: a shell history is not a
procedure. The private repository is where the weights, the drafts and the
records already sit, so this send is not the one that can leak them; what it can
do is put a rewritten history on top of the private copy without saying so, or
send the checkout to the public copy while its name suggests otherwise. Both of
those are checks, and both are refused.

The commit is read before it moves. A checkout with an uncommitted edit, a
credential in the history, a closed directory in the index or a stale README is
refused here as well, because the state that would fail the public send later is
easier to fix before it is on either remote.

Exit status: zero when the commit was sent, or would have been on a dry run; one
when the review refused or the push failed; two when the review could not be
taken at all.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from checks import PRIVATE_RECEIPT_NAME, resolve_receipt  # noqa: E402
from git_ops import ReleaseError, repository_root  # noqa: E402
from posting import add_common_arguments, post_private  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="post_private",
        description="Review this checkout, then send it to the private repository.",
    )
    add_common_arguments(parser)
    parser.add_argument(
        "--remote", default="origin", help="the private remote this checkout is sent to"
    )
    parser.add_argument(
        "--public-remote",
        default="public",
        help="the other copy, which this send must not be addressed to",
    )
    args = parser.parse_args(argv)

    try:
        root = repository_root()
        return post_private(
            root,
            branch=args.branch,
            remote=args.remote,
            public_remote=args.public_remote,
            force=args.force,
            dry_run=args.dry_run,
            receipt=resolve_receipt(root, args.receipt, PRIVATE_RECEIPT_NAME),
        )
    except ReleaseError as error:
        print(f"post_private: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
