"""
Agent events.

A tiny reporter interface so the harness can show what the model is doing
without either side knowing about the other. The TUI supplies a reporter that
prints; every other caller takes the no-op default. Phases, reasoning, streamed
tokens, tool calls, staged edits, and lint results all travel this channel, so
"show the thinking and the relevant actions" is one seam instead of callbacks
threaded through the pipeline.
"""
from __future__ import annotations


class Reporter:
    """A no-op reporter. Override the methods a surface cares about."""

    def phase(self, name: str, detail: str = "") -> None:
        """A stage boundary, e.g. 'reading', 'thinking', 'staging'."""

    def reasoning(self, text: str) -> None:
        """A fragment of the model's reasoning, in order."""

    def content(self, text: str) -> None:
        """A fragment of user-facing model output, in order."""

    def tool(self, name: str, arguments: dict) -> None:
        """The model called a tool."""

    def tool_progress(self, name: str, chars: int) -> None:
        """A tool call is being generated; ``chars`` so far. Throttled by the caller."""

    def stage(self, filepath: str, match: str, diff_lines: int) -> None:
        """An edit was staged into the shadow copy."""

    def lint(self, filepath: str, diagnostics: list[dict]) -> None:
        """A linter reported on a staged file."""

    def error(self, message: str) -> None:
        """Something failed and the user should know."""


NULL_REPORTER = Reporter()
