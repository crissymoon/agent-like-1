"""
Context collection.

The agent flags the files it is talking about and the harness reads them. This
module finds the paths referenced in a message, reads them through the adapter,
and returns a prompt block that already fits the model's window. It is how the
"find, list, read into an adapter" step reaches the model without giving it an
unbounded filesystem tool.
"""
from __future__ import annotations

import re
from pathlib import Path

from config import CONTEXT_CHAR_BUDGET, TEXT_SUFFIXES, TARGET_ROOT
from core import adapter

_TOKEN = re.compile(r"[A-Za-z0-9_@./*\-\][]+")
_GLOB_CHARS = set("*?[]")
_MAX_FILES = 8


def _looks_like_path(token: str) -> bool:
    if token.startswith("@"):
        token = token[1:]
    if any(char in token for char in _GLOB_CHARS):
        return True
    if token.startswith("./") or "/" in token:
        return True
    return Path(token).suffix.lower() in TEXT_SUFFIXES


def extract_paths(message: str, root: Path | None = None, limit: int = _MAX_FILES) -> list[str]:
    """Return the paths referenced in a message, resolved against the root."""
    base = root or TARGET_ROOT
    found: list[str] = []
    for raw in _TOKEN.findall(message):
        token = raw.lstrip("@").strip(".,:;()'\"")
        if not token or not _looks_like_path(token):
            continue
        if any(char in token for char in _GLOB_CHARS):
            for entry in adapter.find_files(token, base, limit=limit):
                if entry.rel_path not in found:
                    found.append(entry.rel_path)
        else:
            try:
                path = adapter.resolve(token, base)
            except adapter.AdapterError:
                continue
            if path.exists() and path.is_file() and token not in found:
                found.append(token)
        if len(found) >= limit:
            break
    return found[:limit]


def build_user_context(
    message: str,
    root: Path | None = None,
    budget: int = CONTEXT_CHAR_BUDGET,
) -> tuple[str, list[str]]:
    """
    Append the referenced files to the user message as prompt-ready context.

    Returns (augmented_message, included_files).
    """
    base = root or TARGET_ROOT
    paths = extract_paths(message, base)
    if not paths:
        return message, []

    bundle = adapter.build_context(paths, root=base, budget=budget)
    if not bundle.text:
        return message, []

    block = (
        "\n\nAttached files (find/list/read via the adapter; read-only):\n"
        f"{bundle.text}"
    )
    return message + block, bundle.files
