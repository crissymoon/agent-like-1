"""Pre-push checks: what must not leave this machine.

The package holds the credential scanner and the list of shapes a credential
takes. It is a package rather than a script so the patterns can be imported by
another check without running the command line, and it stays free of the project
so it can be run on the way to any remote.
"""

from __future__ import annotations

__all__ = ["patterns", "scan_secrets"]
