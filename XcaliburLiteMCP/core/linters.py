"""
Linters.

Detects the right checker for a file, runs it through the guarded runner, and
turns its output into a compact defect list. The list is what the model is given
when it repairs: a precise line and message invites a surgical edit, where
"rewrite the file" invites the whole-file churn the patcher exists to avoid.

Every command is built from a fixed template and executed through the guard, so
no model text ever reaches the shell.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from config import COMMAND_TIMEOUT, LINT_TIMEOUT, TARGET_ROOT
from security import guarded_run


@dataclass
class Diagnostic:
    filepath: str
    line: int
    column: int
    code: str
    message: str
    severity: str = "error"

    def to_dict(self) -> dict:
        return {
            "filepath": self.filepath, "line": self.line, "column": self.column,
            "code": self.code, "message": self.message, "severity": self.severity,
        }


# Suffix -> (tool, command builder). A template is chosen only when the tool is
# on PATH; otherwise the suffix falls through to the next candidate.
_TEMPLATES: dict[str, list[tuple[str, str]]] = {
    ".py": [("ruff", "ruff check --output-format=json --no-cache {path}")],
    ".js": [("node", "node --check {path}")],
    ".mjs": [("node", "node --check {path}")],
    ".ts": [("tsc", "tsc --noEmit {path}")],
    ".php": [("php", "php -l {path}")],
    ".sh": [("shellcheck", "shellcheck --format=json {path}")],
    ".bash": [("shellcheck", "shellcheck --format=json {path}")],
    ".rb": [("ruby", "ruby -c {path}")],
}
# Always-available fallback: compiling bytes catches syntax errors with no tool.
_FALLBACK: dict[str, tuple[str, str]] = {
    ".py": ("python", "{python} -m py_compile {path}"),
    ".json": ("python", "{python} -m json.tool {path}"),
}


def commands_for(rel_path: str) -> list[str]:
    """Every checker command worth trying for a path, most specific first."""
    suffix = Path(rel_path).suffix.lower()
    candidates: list[str] = []
    for tool, template in _TEMPLATES.get(suffix, []):
        if tool == "python" or shutil.which(tool):
            candidates.append(template.format(path=rel_path))
    fallback = _FALLBACK.get(suffix)
    if fallback and (fallback[0] == "python" or shutil.which(fallback[0])):
        candidates.append(fallback[1].format(python=sys.executable, path=rel_path))
    return candidates


def _parse_json(stdout: str, rel_path: str) -> list[Diagnostic]:
    """Parse ruff or shellcheck JSON when the payload is a list of findings."""
    try:
        payload = json.loads(stdout or "[]")
    except json.JSONDecodeError:
        return []
    findings: list[Diagnostic] = []
    if isinstance(payload, dict):
        payload = [payload]
    for item in payload:
        if not isinstance(item, dict):
            continue
        if "code" in item and "filename" in item:            # ruff
            location = item.get("location") or {}
            findings.append(Diagnostic(
                rel_path, location.get("row", 0), location.get("column", 0),
                str(item.get("code", "")), str(item.get("message", "")).strip(),
                "error" if item.get("code", "").startswith("E") else "warning",
            ))
        elif "comments" in item:                              # shellcheck
            for comment in item.get("comments", []):
                findings.append(Diagnostic(
                    rel_path, comment.get("line", 0), comment.get("column", 0),
                    f"SC{comment.get('code', '')}", str(comment.get("message", "")).strip(),
                    "error" if comment.get("level") == "error" else "warning",
                ))
    return findings


_LINE_RE = re.compile(r"^(?P<path>[^\s:]+):(?P<line>\d+):?(?P<col>\d+)?:?\s*(?P<msg>.*)$")
_PY_FRAME_RE = re.compile(r'File "(?P<path>[^"]+)", line (?P<line>\d+)')
_PY_ERROR_RE = re.compile(r"^(?P<kind>\w*(?:Error|Exception|Warning)):?\s*(?P<msg>.*)$", re.MULTILINE)


def _parse_python(text: str, rel_path: str) -> list[Diagnostic]:
    """
    Parse a Python traceback or py_compile report.

    The interpreter prints ``File "path", line N`` frames followed by a bare
    ``SyntaxError: message``, which is not the ``path:line:col`` shape the
    generic parser reads, so it gets its own reader.
    """
    findings: list[Diagnostic] = []
    frames = list(_PY_FRAME_RE.finditer(text or ""))
    errors = list(_PY_ERROR_RE.finditer(text or ""))
    if not errors:
        return findings
    for index, error in enumerate(errors):
        line = int(frames[index].group("line")) if index < len(frames) else 0
        findings.append(Diagnostic(
            rel_path, line, 0, error.group("kind"),
            f"{error.group('kind')}: {error.group('msg')}".strip(": ").strip(),
            "error",
        ))
    return findings


def _parse_text(text: str, rel_path: str) -> list[Diagnostic]:
    """Parse the common ``path:line:col: message`` shape."""
    findings: list[Diagnostic] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        match = _LINE_RE.match(line)
        if not match:
            continue
        column = match.group("col") or "0"
        findings.append(Diagnostic(
            rel_path, int(match.group("line")), int(column), "", match.group("msg").strip(),
            "error" if "error" in line.lower() else "warning",
        ))
    return findings


def lint_file(rel_path: str, root: Path | None = None, target: Path | None = None) -> list[Diagnostic]:
    """
    Run the first working checker for a file and return its findings.

    ``target`` points the checker at a shadow file while its findings are still
    labelled with the repository path the reviewer knows. Returns [] when no
    checker exists or the command was refused.
    """
    base = root or TARGET_ROOT
    check = target or rel_path
    for command in commands_for(rel_path):
        command = command.replace(rel_path, str(check), 1) if target else command
        result = guarded_run.run(command, cwd=base, timeout=min(COMMAND_TIMEOUT, LINT_TIMEOUT))
        if not result.get("allowed"):
            continue
        if result.get("success"):
            return []
        stdout = result.get("stdout", "")
        stderr = result.get("stderr", "")
        findings = _parse_json(stdout, rel_path) or _parse_json(stderr, rel_path)
        if not findings:
            findings = _parse_python(stderr, rel_path) or _parse_python(stdout, rel_path)
        if not findings:
            findings = _parse_text(stdout, rel_path) or _parse_text(stderr, rel_path)
        if findings:
            return findings
        if result.get("error"):
            return [Diagnostic(rel_path, 0, 0, "", result["error"], "error")]
    return []


def format_diagnostics(diagnostics: list[Diagnostic], limit: int = 40) -> str:
    """A compact, line-anchored defect list for a repair prompt."""
    if not diagnostics:
        return ""
    lines = []
    for item in diagnostics[:limit]:
        where = f"{item.filepath}:{item.line}:{item.column}" if item.line else item.filepath
        code = f"[{item.code}] " if item.code else ""
        lines.append(f"{where}: {code}{item.message}")
    if len(diagnostics) > limit:
        lines.append(f"... and {len(diagnostics) - limit} more")
    return "\n".join(lines)


def lint_shadow(shadow_path: Path, rel_path: str, root: Path | None = None) -> list[Diagnostic]:
    """Lint a staged shadow file, reporting findings against the repository path."""
    return lint_file(rel_path, root=root, target=Path(shadow_path))
