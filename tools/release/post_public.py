"""Send a reviewed commit to the public copy, once the private one already has it.

    python3 tools/release/post_public.py --dry-run   # read, and report the push
    python3 tools/release/post_public.py             # read, then send

This is the second of the two sends and the one that cannot be undone. A file
that reaches a public remote has been read, indexed and possibly cloned, so the
commit is read here before it moves and it is refused when anything in it
belongs on this machine.

Two preconditions are this command's own. The commit has to be in the private
remote already, because a public copy that is ahead of the private one is a
history neither rewrite can reconcile without hand work. And the destination has
to be a different remote from the private one, resolved by URL rather than by
name, because a send to the private repository looks exactly like a publication
that worked.

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

from checks import PUBLIC_RECEIPT_NAME, resolve_receipt  # noqa: E402
from git_ops import ReleaseError, repository_root  # noqa: E402
from posting import add_common_arguments, post_public  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="post_public",
        description="Review a private checkout, then send the reviewed commit to the public copy.",
    )
    add_common_arguments(parser)
    parser.add_argument(
        "--remote", default="public", help="the remote the public copy lives in"
    )
    parser.add_argument(
        "--private-remote",
        default="origin",
        help="the remote that has to hold the commit already",
    )
    args = parser.parse_args(argv)

    try:
        root = repository_root()
        return post_public(
            root,
            branch=args.branch,
            remote=args.remote,
            private_remote=args.private_remote,
            force=args.force,
            dry_run=args.dry_run,
            receipt=resolve_receipt(root, args.receipt, PUBLIC_RECEIPT_NAME),
        )
    except ReleaseError as error:
        print(f"post_public: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
