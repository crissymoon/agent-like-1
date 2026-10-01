"""Decide what a file is, then run the checker that answers for that type.

This is the preflight for a file that is about to be handed to a model, or one
that a model has just produced. It is cheap on purpose: every checker is a
parser already installed on the machine, and the only one that needs a browser
is the diagram, which is drawn in the mermaid build already vendored beside it.

    from lint import lint_path
    report = lint_path(Path("mermaid-viewer/diagram.mmd"))

`lint_text` is the same question asked of a string, which is how a candidate
file can be parsed before it is written anywhere. `supported_languages` says
which languages have a checker, so a caller can tell an unchecked file from a
clean one.

Nothing here calls a model, reaches the network, or rewrites a file on its own.
"""

from __future__ import annotations

from .checkers import Options, lint_path, lint_text, supported_languages
from .detect import FileKind, detect_file, read_text
from .findings import Finding, Report, Severity
from .mermaid import bundle_path, find_chrome

__all__ = [
    "FileKind",
    "Finding",
    "Options",
    "Report",
    "Severity",
    "bundle_path",
    "detect_file",
    "find_chrome",
    "lint_path",
    "lint_text",
    "read_text",
    "supported_languages",
]
