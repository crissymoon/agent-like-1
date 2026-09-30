"""The markdown copy of a conversation, written after a text run.

A run in a terminal leaves nothing behind, which is the wrong default when the
point is to compare models. So a text run writes a document beside the other
recorded work, and the three things a reader wants first are the first three
things in it: which model, which file, and how long it took.

Two rules about what goes in it.

Machine paths are shortened on the way in, by the same rule the rest of the
project uses (`lib/PathRecord.php`, `tools/normalize_paths.py`). A transcript is
committed, so a transcript that names the account or the checkout directory is a
transcript that cannot be. The repository enforces this from the other side too:
the secret scanner refuses a home directory path, so a careless record blocks a
commit rather than shipping quietly.

Measured numbers are recorded as measured, and an unmeasurable one is recorded as
not measured. A token rate that was estimated from a character count is worse
than no rate, because it reads like a measurement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import paths

#: Byte formatting lives with the other modules that report the same numbers, so
#: a size quoted here and a size quoted by an image run are the same arithmetic.
from tools.imgmodels.sizes import human_bytes

#: The order of the first block. Model, path and time lead because they are what
#: a reader compares across two transcripts; everything else supports them.
LEAD_ROWS: tuple[str, ...] = ("Model", "Model path", "Time taken")


@dataclass(frozen=True)
class Turn:
    """One message, from either side."""

    role: str
    content: str
    seconds: float | None = None
    tokens: int | None = None

    @property
    def is_user(self) -> bool:
        return self.role == "user"


@dataclass
class Report:
    """Everything a transcript records, gathered rather than guessed at."""

    key: str
    label: str
    model_path: Path
    file_bytes: int | None = None
    quantisation: str = ""
    architecture: str = ""
    parameters: int | None = None
    context: int | None = None
    runtime: str = ""
    device: str = ""
    mode_label: str = ""
    temperature: float = 0.0
    started: datetime = field(default_factory=datetime.now)
    seconds: float = 0.0
    turns: list[Turn] = field(default_factory=list)
    prompt_tokens: int | None = None
    generated_tokens: int | None = None
    time_to_first_token: float | None = None
    command: str = ""
    notes: list[str] = field(default_factory=list)
    root: Path | None = None

    def rows(self) -> list[tuple[str, str]]:
        """The header, in the order it is printed."""
        rate = None
        if self.generated_tokens and self.time_to_first_token is not None:
            decode = self.seconds - self.time_to_first_token
            if decode > 0:
                rate = f"{self.generated_tokens / decode:.1f} tokens/s"
        return [
            ("Model", self.label),
            ("Model path", paths.record(self.model_path, self.root)),
            ("Time taken", f"{self.seconds:.1f}s"),
            ("Model file", human_bytes(self.file_bytes)),
            ("Quantisation", self.quantisation or "-"),
            ("Architecture", self.architecture or "-"),
            ("Parameters", f"{self.parameters:,}" if self.parameters else "-"),
            ("Context", f"{self.context:,}" if self.context else "-"),
            ("Mode", self.mode_label or "-"),
            ("Temperature", f"{self.temperature:g}"),
            ("Runtime", self.runtime or "-"),
            ("Device", self.device or "-"),
            ("Started", self.started.strftime("%Y-%m-%d %H:%M:%S")),
            ("Turns", str(sum(1 for turn in self.turns if turn.is_user))),
            ("Prompt tokens", f"{self.prompt_tokens:,}" if self.prompt_tokens else "not measured"),
            ("Generated tokens", f"{self.generated_tokens:,}" if self.generated_tokens else "not measured"),
            ("First token", f"{self.time_to_first_token:.2f}s" if self.time_to_first_token is not None else "not measured"),
            ("Throughput", rate or "not measured"),
        ]

    def cells(self) -> dict[str, str]:
        return dict(self.rows())

    def document(self) -> str:
        """The whole file, as one string."""
        cells = self.cells()
        lines = [f"# {self.label}", ""]
        lines.append("| field | value |")
        lines.append("| --- | --- |")
        for name in LEAD_ROWS:
            lines.append(f"| {name} | {cells[name]} |")
        for name, value in self.rows():
            if name in LEAD_ROWS:
                continue
            lines.append(f"| {name} | {value} |")
        lines.append("")

        lines.append("## Conversation")
        lines.append("")
        if not self.turns:
            lines.append("No turns were exchanged in this run.")
            lines.append("")
        for turn in self.turns:
            lines.append(f"### {'You' if turn.is_user else 'Model'}")
            lines.append("")
            if not turn.is_user and turn.seconds is not None:
                detail = f"_{turn.seconds:.1f}s"
                if turn.tokens:
                    detail += f", {turn.tokens} tokens"
                lines.append(f"{detail}_")
                lines.append("")
            lines.append(turn.content.strip() or "_(empty)_")
            lines.append("")

        if self.notes:
            lines.append("## Notes")
            lines.append("")
            lines.extend(f"- {note}" for note in self.notes)
            lines.append("")

        lines.append("## How this was produced")
        lines.append("")
        lines.append("```")
        lines.append(self.command or "not recorded")
        lines.append("```")
        lines.append("")
        return "\n".join(lines)


def write(report: Report, folder: Path) -> Path:
    """Write the transcript and return where it went."""
    folder.mkdir(parents=True, exist_ok=True)
    stamp = report.started.strftime("%Y%m%d-%H%M%S")
    path = folder / f"{report.key}-{stamp}.md"
    path.write_text(report.document(), encoding="utf-8")
    return path
