"""
The chunker.

Splits large files into overlapping windows so a single file can never blow the
context window. The overlap keeps a symbol that straddles a boundary present in
both windows, which is what lets the model reason about a seam without losing it.
"""
from __future__ import annotations

from dataclasses import dataclass

from config import CHUNK_MAX_CHARS, CHUNK_OVERLAP_LINES
from core import adapter


@dataclass
class Chunk:
    index: int
    start_line: int      # 1-indexed, inclusive
    end_line: int        # 1-indexed, inclusive
    text: str

    @property
    def label(self) -> str:
        return f"lines {self.start_line}-{self.end_line}"


def chunk_lines(
    text: str,
    max_chars: int = CHUNK_MAX_CHARS,
    overlap_lines: int = CHUNK_OVERLAP_LINES,
) -> list[Chunk]:
    """
    Split text into overlapping chunks bounded by characters.

    A chunk closes once it would exceed ``max_chars``; the next one rewinds by
    ``overlap_lines`` so consecutive windows share context.
    """
    lines = text.splitlines(keepends=True)
    if not lines:
        return []

    chunks: list[Chunk] = []
    index = 0
    start = 0  # zero-based cursor into lines

    while start < len(lines):
        used = 0
        end = start
        while end < len(lines) and (used + len(lines[end]) <= max_chars or end == start):
            used += len(lines[end])
            end += 1

        chunks.append(Chunk(
            index=index,
            start_line=start + 1,
            end_line=end,
            text="".join(lines[start:end]),
        ))
        index += 1

        if end >= len(lines):
            break
        # Rewind for overlap, but always make forward progress.
        start = max(end - overlap_lines, start + 1)

    return chunks


def chunk_file(rel_path: str, **kwargs) -> list[Chunk]:
    """Read a file through the adapter and chunk it."""
    read = adapter.read_text(rel_path)
    return chunk_lines(read.content, **kwargs)


def chunks_for_prompt(chunks: list[Chunk], budget: int) -> list[Chunk]:
    """Return the leading chunks that fit inside a character budget."""
    selected: list[Chunk] = []
    used = 0
    for chunk in chunks:
        if used + len(chunk.text) > budget:
            break
        selected.append(chunk)
        used += len(chunk.text)
    return selected
