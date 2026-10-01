"""The fixes that are mechanical enough to make without reading the file.

Three, and the shortness of the list is the design. A fixer here has to be
idempotent, has to be reversible by inspection, and has to be unable to change
what a file means:

    trailing-whitespace   the space before a newline, which nothing reads
    final-newline         one newline at the end, which a diff and a tool both expect
    crlf                  a carriage return, which changes how a file compares

What is deliberately not here is worth naming, because it is what a linter is
usually asked for first:

    a missing semicolon   in a mermaid diagram there is no such fault. A semicolon
                          there separates statements that a newline already
                          separates, so a rule that inserted one would be a rule
                          that rewrote a diagram to look like the tool's taste.
    a missing semicolon   in javascript or php is a real fault, and inserting one
                          decides where a statement ends, which is a decision the
                          author makes and a parser cannot.
    a nesting error       mermaid keeps a node in the subgraph where it was first
                          named. Which group it should be in is the author's
                          intent, so the checker reports it and nothing moves it.
    an unbalanced brace   a parser can say where it stopped, and only the author
                          can say which of the two repairs was meant.

Every fix is reported with the lines it touched, so a run that rewrites a file
can say what it did to it. Nothing regroups, reindents or requotes anything.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

TRAILING_WHITESPACE = re.compile(r"[ \t]+(?=\n)")

#: Lines with content, paired with the changes made to them, are what a fix
#: reports. A count alone would not say where to look.
Fix = Callable[[str], tuple[str, list[int]]]


@dataclass(frozen=True)
class Edit:
    """One fix, named, with the reason it is safe to make without reading more."""

    code: str
    description: str
    apply: Fix


def _strip_trailing_whitespace(text: str) -> tuple[str, list[int]]:
    touched = [
        number
        for number, line in enumerate(text.splitlines(), start=1)
        if line != line.rstrip(" \t")
    ]
    if not touched:
        return text, []
    return "\n".join(line.rstrip(" \t") for line in text.splitlines()) + (
        "\n" if text.endswith("\n") else ""
    ), touched


def _final_newline(text: str) -> tuple[str, list[int]]:
    if not text or text.endswith("\n"):
        return text, []
    return text + "\n", [len(text.splitlines())]


def _crlf(text: str) -> tuple[str, list[int]]:
    if "\r\n" not in text:
        return text, []
    touched = [number for number, line in enumerate(text.splitlines(), start=1) if line.endswith("\r")]
    return text.replace("\r\n", "\n"), touched


#: The fixes, in the order they are applied. Stripping whitespace first means
#: the newline that is added second is the only one at the end.
EDITS: tuple[Edit, ...] = (
    Edit("trailing-whitespace", "remove the whitespace before a newline", _strip_trailing_whitespace),
    Edit("crlf", "write the newlines as newlines rather than carriage returns", _crlf),
    Edit("final-newline", "end the file with one newline", _final_newline),
)


def plan(text: str) -> list[tuple[str, list[int]]]:
    """What each fix would do to this text, without changing it."""
    pending: list[tuple[str, list[int]]] = []
    current = text
    for edit in EDITS:
        candidate, touched = edit.apply(current)
        if touched:
            pending.append((edit.code, touched))
        current = candidate
    return pending


def apply(text: str) -> tuple[str, list[tuple[str, list[int]]]]:
    """Make every fix, and say which lines each one touched."""
    made: list[tuple[str, list[int]]] = []
    current = text
    for edit in EDITS:
        candidate, touched = edit.apply(current)
        if touched:
            made.append((edit.code, touched))
        current = candidate
    return current, made
