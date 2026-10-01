"""One shape for everything a checker has to say.

The three severities mean three different things, and the difference is what
decides whether a run is allowed to call itself clean:

    error    the file will not load. php refuses it, node refuses it, mermaid
             fails to parse it. Something has to change.
    warning  the file loads and does not mean what it says. mermaid drops a
             style property it does not recognise and says nothing, so a typo
             in `classDef` is an error the reader never sees.
    note     a fact worth recording that is not a defect: a checker could not
             run because its binary is not installed, or a fixable line is not
             the shape the file is written in.

A report never says a file is clean when a checker was skipped. A skipped check
is a note in the same list, so "no findings" cannot be read as "the checker ran
and found nothing" when it did not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

#: The order a finding is compared against a fail level. Higher is more serious.
_ORDER: dict["Severity", int] = {}


class Severity(str, Enum):
    NOTE = "note"
    WARNING = "warning"
    ERROR = "error"

    @property
    def rank(self) -> int:
        return _ORDER[self]

    def at_least(self, other: "Severity") -> bool:
        return self.rank >= other.rank


_ORDER.update({Severity.NOTE: 0, Severity.WARNING: 1, Severity.ERROR: 2})


@dataclass(frozen=True)
class Finding:
    """One thing a checker noticed, anchored to a place in one file."""

    path: str
    severity: Severity
    code: str
    message: str
    line: int | None = None
    column: int | None = None
    hint: str | None = None

    def where(self) -> str:
        if self.line is None:
            return self.path
        if self.column is None:
            return f"{self.path}:{self.line}"
        return f"{self.path}:{self.line}:{self.column}"

    def format(self) -> str:
        head = f"{self.where()}: {self.severity.value}: {self.code}: {self.message}"
        return f"{head}\n    {self.hint}" if self.hint else head


@dataclass
class Report:
    """Everything said about one file, in the order the checkers said it."""

    path: str
    language: str
    evidence: str
    findings: list[Finding] = field(default_factory=list)
    fixes: list[str] = field(default_factory=list)

    def add(
        self,
        severity: Severity,
        code: str,
        message: str,
        line: int | None = None,
        column: int | None = None,
        hint: str | None = None,
    ) -> Finding:
        finding = Finding(self.path, severity, code, message, line, column, hint)
        self.findings.append(finding)
        return finding

    def extend(self, findings: list[Finding]) -> None:
        self.findings.extend(findings)

    def counts(self) -> dict[str, int]:
        tally = {severity.value: 0 for severity in Severity}
        for finding in self.findings:
            tally[finding.severity.value] += 1
        return tally

    def at_least(self, level: Severity) -> list[Finding]:
        return [finding for finding in self.findings if finding.severity.at_least(level)]

    def summary(self) -> str:
        tally = self.counts()
        return (
            f"{tally['error']} error(s), {tally['warning']} warning(s), "
            f"{tally['note']} note(s)"
        )

    def lines(self) -> list[str]:
        head = f"{self.path}: {self.language} ({self.evidence}) - {self.summary()}"
        if self.fixes:
            head += f", {len(self.fixes)} fix(es)"
        body = [finding.format() for finding in self.findings]
        return [head, *body]

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "language": self.language,
            "evidence": self.evidence,
            "counts": self.counts(),
            "fixes": self.fixes,
            "findings": [
                {
                    "severity": finding.severity.value,
                    "code": finding.code,
                    "message": finding.message,
                    "line": finding.line,
                    "column": finding.column,
                    "hint": finding.hint,
                }
                for finding in self.findings
            ],
        }
