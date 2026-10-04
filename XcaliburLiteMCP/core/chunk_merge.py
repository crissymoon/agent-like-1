"""
Chunk planning and merge.

Reading a large file is done in overlapping windows so the context window is
never overrun, and the overlap keeps a symbol that straddles a seam visible in
both neighbours. Editing the same way needs the inverse operation: this module
plans the windows, renders them for the model, and stitches per-window results
back into one document, trimming the overlap and reporting any line that two
windows both changed. That merge is the heavy lifting taken off the model.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from config import CHUNK_MAX_CHARS, CHUNK_OVERLAP_LINES, TARGET_ROOT
from core import adapter, file_chunker
from core.file_chunker import Chunk


@dataclass
class ChunkPlan:
    rel_path: str
    total_lines: int
    chunks: list[Chunk]

    @property
    def chunked(self) -> bool:
        return len(self.chunks) > 1


def plan(rel_path: str, root: Path | None = None, max_chars: int = CHUNK_MAX_CHARS,
         overlap_lines: int = CHUNK_OVERLAP_LINES, text: str | None = None) -> ChunkPlan:
    """Read a file through the adapter and split it into overlapping windows."""
    base = root or TARGET_ROOT
    content = text if text is not None else adapter.read_text(rel_path, root=base).content
    chunks = file_chunker.chunk_lines(content, max_chars=max_chars, overlap_lines=overlap_lines)
    return ChunkPlan(rel_path, content.count("\n") + 1, chunks)


def render_plan(plan: ChunkPlan) -> str:
    """Render every window for a prompt, labelled with its line range."""
    blocks = [
        f'<file path="{plan.rel_path}" lines="{plan.total_lines}" chunk="{chunk.label}">\n'
        f"{chunk.text}\n</file>"
        for chunk in plan.chunks
    ]
    return "\n".join(blocks)


def merge_chunk_texts(original: str, edited: list[tuple[Chunk, str]]) -> tuple[str, list[str]]:
    """
    Stitch edited windows back into one document.

    Windows arrive in order. The first window to own a line wins; a later window
    that overlaps it contributes only the lines past the shared seam, and any
    overlap line both windows changed is reported as a conflict so the reviewer
    sees the seam rather than a silent overwrite. Returns (content, conflicts).
    """
    origin_lines = original.splitlines(keepends=True)
    out: list[str] = []
    conflicts: list[str] = []
    covered: dict[int, int] = {}     # original line number -> chunk index that owns it
    cursor = 1                       # 1-indexed next original line to emit

    for chunk, new_text in sorted(edited, key=lambda pair: pair[0].start_line):
        new_lines = new_text.splitlines(keepends=True)
        keep_from = max(chunk.start_line, cursor)
        skip = keep_from - chunk.start_line

        for line_no in range(chunk.start_line, min(cursor, chunk.end_line + 1)):
            owner = covered.get(line_no)
            if owner is not None and owner != chunk.index:
                conflicts.append(f"line {line_no}: windows {owner} and {chunk.index} both changed it")

        if chunk.start_line > cursor:
            out.extend(origin_lines[cursor - 1:chunk.start_line - 1])
        out.extend(new_lines[skip:])

        for line_no in range(keep_from, chunk.end_line + 1):
            covered[line_no] = chunk.index
        cursor = max(cursor, chunk.end_line + 1)

    out.extend(origin_lines[cursor - 1:])
    return "".join(out), conflicts


def merge_edit_results(original: str, models: list[tuple[Chunk, str]]) -> tuple[str, str, list[str]]:
    """
    Merge, then diff the result against the original.

    Returns (content, unified_diff, conflicts) so a caller can stage the merged
    document without re-deriving the change.
    """
    from core import surgical_patcher

    merged, conflicts = merge_chunk_texts(original, models)
    return merged, surgical_patcher.unified_diff(original, merged, ""), conflicts
