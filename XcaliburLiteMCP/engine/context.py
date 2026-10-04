"""
Context collection.

The agent flags the files it is talking about and the harness reads them. Paths
are found with the interpreter, so a loose reference in a sentence resolves to
the file the user meant, and each file is rendered into prompt-ready text that
already fits the model's window. A large file is split into overlapping windows
with the chunk planner, so the seam between two windows stays visible instead of
being where context runs out.
"""
from __future__ import annotations

from pathlib import Path

from config import CHUNK_MAX_CHARS, CONTEXT_CHAR_BUDGET, TARGET_ROOT
from core import adapter, chunk_merge
from core.interpreter import FileIndex


def extract_paths(message: str, root: Path | None = None, cwd: str = "",
                  limit: int = 8) -> list[str]:
    """Return the real files referenced in a message, resolved by the interpreter."""
    base = (root or TARGET_ROOT).resolve()
    index = FileIndex(base)
    return [resolution.path for resolution in index.mentions(message, cwd=cwd, limit=limit)]


def render_file(rel: str, root: Path, base_text: str | None = None,
                budget_left: int = CONTEXT_CHAR_BUDGET) -> tuple[list[str], str]:
    """
    Render one file for a prompt.

    Small files are included whole. A file over the chunk threshold is rendered
    as labelled, overlapping windows. Returns (blocks, status) where status is
    "included", "omitted" (too large for the remaining budget), or "new".
    """
    if base_text is not None:
        content = base_text
        total_lines = content.count("\n") + 1
    else:
        try:
            read = adapter.read_text(rel, root=root)
        except adapter.AdapterError:
            path = adapter.resolve(rel, root)
            if path.exists():
                return [], "omitted"
            return [f'<file path="{rel}" status="new"/>'], "new"
        content = read.content
        total_lines = read.total_lines

    if len(content) <= CHUNK_MAX_CHARS:
        return [f'<file path="{rel}" lines="{total_lines}">\n{content}\n</file>'], "included"

    plan = chunk_merge.plan(rel, root=root, text=content)
    blocks: list[str] = []
    for chunk in plan.chunks:
        block = (
            f'<file path="{rel}" lines="{total_lines}" chunk="{chunk.label}">\n'
            f"{chunk.text}\n</file>"
        )
        if budget_left and len(block) > budget_left and blocks:
            break
        blocks.append(block)
    return blocks, "included"


def build_user_context(
    message: str,
    root: Path | None = None,
    budget: int = CONTEXT_CHAR_BUDGET,
    cwd: str = "",
) -> tuple[str, list[str], dict[str, str]]:
    """
    Append the referenced files to the user message as prompt-ready context.

    Returns (augmented_message, included_files, resolutions) where resolutions
    maps each matched path to the confidence label that matched it, so the
    caller can show the user what it green-lit.
    """
    base = (root or TARGET_ROOT).resolve()
    index = FileIndex(base)
    resolutions = index.mentions(message, cwd=cwd)
    if not resolutions:
        return message, [], {}

    used = 0
    blocks: list[str] = []
    included: list[str] = []
    labels: dict[str, str] = {}
    for resolution in resolutions:
        if used >= budget:
            break
        rendered, status = render_file(resolution.path, base, budget_left=budget - used)
        if status == "omitted":
            continue
        joined = "\n".join(rendered)
        if used + len(joined) > budget and included:
            continue
        blocks.extend(rendered)
        used += len(joined)
        included.append(resolution.path)
        labels[resolution.path] = f"{resolution.kind} {resolution.confidence:.2f}"

    if not blocks:
        return message, [], {}

    block = (
        "\n\nAttached files (find/list/read via the adapter; read-only):\n"
        + "\n".join(blocks)
    )
    return message + block, included, labels
