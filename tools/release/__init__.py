"""Reviewing a private checkout before any of it is published.

This repository is the private one. A second, public repository holds the copy
that leaves the machine, and what goes there is not pushed from a working tree
but from a commit that has already passed a review. The review lives here rather
than in a procedure document, because a procedure in prose is a procedure that
gets skimmed while a command either passes or does not.

    checks    the battery, as data, and the manifest of the commit it read
    publish   sending a reviewed commit to the public remote, and nothing else
    release   the command line over both

The list of directories that must stay on this machine is deliberately not
restated here. It is read from `tools/security/scan_secrets.py`, which is what
the pre-push hook runs, so the review and the hook cannot come to disagree
about what is closed.
"""

from __future__ import annotations

__all__ = ["checks", "publish", "release"]
