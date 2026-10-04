"""
The surgical patcher.

Applies precise, targeted edits rather than whole-file rewrites, and never
touches the live tree. Every edit is computed in memory, written into a
timestamped shadow directory, and paired with a unified diff for review.
"""
from __future__ import annotations

import difflib
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from config import ORIGINAL_COPY, SHADOW_COPY
from core import adapter


class PatchError(Exception):
    """Raised when an edit cannot be applied unambiguously."""


@dataclass
class PatchResult:
    rel_path: str
    new_content: str
    diff: str
    start_line: int
    lines_removed: int
    lines_added: int
    match: str            # "exact" | "normalized" | "regex"
    original_exists: bool


def _normalized(line: str) -> str:
    return " ".join(line.split())


def _find_exact(original: str, old_str: str) -> int:
    count = original.count(old_str)
    if count == 0:
        return -1
    if count > 1:
        raise PatchError(
            f"old_str appears {count} times; add surrounding context to make it unique"
        )
    return original.index(old_str)


def _find_normalized(original: str, old_str: str) -> tuple[int, int]:
    """
    Match on whitespace-insensitive lines. Returns (char_start, char_end).

    This is the recovery path for the common failure mode where the model
    reproduces code correctly but not the exact indentation.
    """
    original_lines = original.splitlines(keepends=True)
    old_lines = old_str.strip("\n").splitlines()
    if not old_lines:
        return -1, -1

    want = [_normalized(line) for line in old_lines]
    have = [_normalized(line) for line in original_lines]

    for index in range(0, len(have) - len(want) + 1):
        if have[index:index + len(want)] == want:
            start = sum(len(line) for line in original_lines[:index])
            end = start + sum(len(line) for line in original_lines[index:index + len(want)])
            return start, end
    return -1, -1


def apply_edit(original: str, old_str: str, new_str: str, regex: bool = False) -> PatchResult:
    """Compute a new document from one edit. Raises PatchError if ambiguous."""
    path_label = ""
    if regex:
        compiled = re.compile(old_str)
        found = list(compiled.finditer(original))
        if not found:
            raise PatchError("regex pattern not found")
        if len(found) > 1:
            raise PatchError(f"regex matches {len(found)} places; make it more specific")
        match_obj = found[0]
        start, end = match_obj.start(), match_obj.end()
        new_content = original[:start] + new_str + original[end:]
        match_kind = "regex"
    else:
        start = _find_exact(original, old_str)
        if start >= 0:
            end = start + len(old_str)
            match_kind = "exact"
        else:
            start, end = _find_normalized(original, old_str)
            if start < 0:
                raise PatchError("old_str not found (even allowing whitespace differences)")
            match_kind = "normalized"
        new_content = original[:start] + new_str + original[end:]

    start_line = original[:start].count("\n") + 1
    return PatchResult(
        rel_path=path_label,
        new_content=new_content,
        diff=unified_diff(original, new_content, path_label),
        start_line=start_line,
        lines_removed=original[start:end].count("\n") + 1,
        lines_added=new_str.count("\n") + 1,
        match=match_kind,
        original_exists=True,
    )


def unified_diff(old: str, new: str, rel_path: str, context: int = 3) -> str:
    """Produce a standard unified diff, labelled with the file path."""
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"a/{rel_path}",
        tofile=f"b/{rel_path}",
        n=context,
    )
    return "".join(diff)


def timestamp_label(moment: datetime | None = None) -> str:
    return (moment or datetime.now()).strftime("%Y%m%d-%H%M%S")


def snapshot_original(rel_path: str, root: Path, original_dir: Path = ORIGINAL_COPY) -> Path | None:
    """
    Copy a file's pristine state into original_copy (first write wins).

    Returns the snapshot path, or None when the file does not yet exist.
    """
    source = adapter.resolve(rel_path, root)
    if not source.exists() or source.is_dir():
        return None
    destination = original_dir / rel_path
    if destination.exists():
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return destination


def write_shadow(
    rel_path: str,
    content: str,
    shadow_root: Path = SHADOW_COPY,
    stamp: str | None = None,
) -> Path:
    """Write a proposed version into shadow_copy/<timestamp>/<rel_path>."""
    target = shadow_root / (stamp or timestamp_label()) / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def stage(
    rel_path: str,
    root: Path,
    old_str: str,
    new_str: str,
    *,
    regex: bool = False,
    shadow_root: Path = SHADOW_COPY,
    original_dir: Path = ORIGINAL_COPY,
    stamp: str | None = None,
    base_text: str | None = None,
) -> dict:
    """
    Full staging pipeline for one edit: snapshot, apply, shadow, diff.

    ``base_text`` overrides the text the edit is applied to, which is how a
    rework stacks a revision on top of a previously proposed shadow instead of
    the pristine original. Returns a dict describing the staged edit.
    """
    original = base_text if base_text is not None else adapter.read_for_edit(rel_path, root)
    existed = adapter.resolve(rel_path, root).exists()
    snapshot_original(rel_path, root, original_dir)

    if not existed:
        # The file does not exist, so there is nothing to match against: the
        # proposed text becomes the new file. This also covers the common case
        # where the model writes a new file but still supplies an old_str
        # template because it assumed the file was already there.
        if not new_str:
            raise PatchError("cannot create an empty file")
        result = PatchResult(
            rel_path, new_str, unified_diff("", new_str, rel_path), 1,
            0, new_str.count("\n") + 1, "create", False,
        )
    else:
        if not old_str:
            raise PatchError(
                "old_str is empty but the file already exists; provide the exact text to replace"
            )
        result = apply_edit(original, old_str, new_str, regex=regex)
        result.rel_path = rel_path
        result.diff = unified_diff(original, result.new_content, rel_path)
        result.original_exists = existed

    stamp = stamp or timestamp_label()
    shadow_path = write_shadow(rel_path, result.new_content, shadow_root, stamp)

    return {
        "filepath": rel_path,
        "match": result.match,
        "start_line": result.start_line,
        "lines_removed": result.lines_removed,
        "lines_added": result.lines_added,
        "diff": result.diff,
        "shadow_path": str(shadow_path),
        "shadow_stamp": stamp,
        "original_exists": result.original_exists,
        "new_content": result.new_content,
    }
