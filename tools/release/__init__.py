"""Sending a reviewed commit, to the private repository and then to the public one.

This repository is the private one. A second, public repository holds the copy
that leaves the machine, and what goes there is not pushed from a working tree
but from a commit that has already passed a review. The review lives here rather
than in a procedure document, because a procedure in prose is a procedure that
gets skimmed while a command either passes or does not.

    git_ops     reaching a remote, and wording the push that goes to it
    checks      the battery, as data, and the manifest of the commit it read
    posting     the two sends, which differ in what they refuse
    post_private  the command line for the private repository
    post_public   the command line for the copy that leaves the machine

The list of directories that must stay on this machine is deliberately not
restated here. It is read from `tools/security/scan_secrets.py`, which is what
the pre-push hook runs, so the review and the hook cannot come to disagree
about what is closed.
"""

from __future__ import annotations

__all__ = ["checks", "git_ops", "posting", "post_private", "post_public"]
