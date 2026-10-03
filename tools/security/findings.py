"""What a security check found, and how serious it is.

The secret scanner has its own type, because what it reports is a credential at
a line and that shape is already read by the release review. This module is for
the checks that came after it: a construct that is a question rather than a
breach, a file mode that is wider than it needs to be, a dependency that is not
pinned. They report the same way, so they share one type, one ordering and one
rendering.

A severity is a claim about what has to happen next rather than about how worried
a reader should be:

    high    a defect that should stop a push. Either it is exploitable as read,
            or it is a property the code says it has and does not.
    medium  a place that is safe only because of something else nearby. It is
            where the defect will be written, so it is worth a look now.
    low     hygiene. Nothing is wrong today and the file is wider than it needs.
    note    a reading taken rather than a finding made: a check that was not
            run, an exception that was reviewed, a fact worth carrying.

`--fail-on` names the level that decides the exit status, and the default is
`high`. A check that could not run raises rather than returning an empty list,
because a scan that did not happen is not a scan that passed.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum


class Severity(Enum):
    """How serious a finding is, ordered so a level can be compared."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NOTE = "note"

    @property
    def rank(self) -> int:
        return _RANKS[self]


_RANKS = {
    Severity.HIGH: 3,
    Severity.MEDIUM: 2,
    Severity.LOW: 1,
    Severity.NOTE: 0,
}


@dataclass(frozen=True)
class Finding:
    """One thing a check has to say about one place in the tree."""

    code: str
    severity: Severity
    path: str
    message: str
    line: int = 0
    hint: str = ""
    excerpt: str = ""

    def render(self) -> str:
        where = f"{self.path}:{self.line}" if self.line else self.path
        text = f"{self.severity.value:<6} {where}  [{self.code}]  {self.message}"
        if self.excerpt:
            text += f"\n       {self.excerpt}"
        if self.hint:
            text += f"\n       {self.hint}"
        return text


@dataclass
class Report:
    """Every finding one check produced, and what it read to produce them."""

    name: str
    surface: str = ""
    findings: list[Finding] = field(default_factory=list)
    counted: dict[str, int] = field(default_factory=dict)

    def add(
        self,
        code: str,
        severity: Severity,
        path: str,
        message: str,
        line: int = 0,
        hint: str = "",
        excerpt: str = "",
    ) -> Finding:
        finding = Finding(code, severity, path, message, line, hint, excerpt)
        self.findings.append(finding)

        return finding

    def extend(self, findings: list[Finding]) -> None:
        self.findings.extend(findings)

    def tally(self, key: str, amount: int = 1) -> None:
        """Count something that is not a finding: an exception, a file skipped."""
        self.counted[key] = self.counted.get(key, 0) + amount

    def counts(self) -> dict[str, int]:
        return {
            severity.value: sum(1 for finding in self.findings if finding.severity is severity)
            for severity in Severity
        }

    def at(self, level: Severity) -> list[Finding]:
        """The findings at or above a level, most serious first."""
        return sorted(
            (finding for finding in self.findings if finding.severity.rank >= level.rank),
            key=lambda finding: (-finding.severity.rank, finding.path, finding.line),
        )

    def summary(self) -> str:
        counts = self.counts()
        parts = ", ".join(
            f"{counts[severity.value]} {severity.value}"
            for severity in (Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.NOTE)
        )
        extra = ""
        if self.counted:
            extra = " (" + ", ".join(f"{key} {value}" for key, value in sorted(self.counted.items())) + ")"
        return f"{self.name}: {parts}{extra}"

    def as_document(self, surface: str | None = None) -> dict:
        counts = self.counts()
        return {
            "schema_version": "1",
            "document": "security-scan",
            "surface": surface if surface is not None else self.surface,
            "name": self.name,
            "clean": not self.findings,
            "counts": {**counts, **{f"counted_{key}": value for key, value in sorted(self.counted.items())}},
            "findings": [
                {**asdict(finding), "severity": finding.severity.value} for finding in self.findings
            ],
        }

    def to_json(self) -> str:
        return json.dumps(self.as_document(), indent=2)
