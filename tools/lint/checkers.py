"""The checker for the type the file turned out to be.

Every checker is the cheapest thing that can answer for its language, and every
one of them is already installed rather than fetched:

    python      this interpreter compiles the source, in process, writing nothing
    json        the standard library parses it
    php         `php -l` parses it and exits non-zero on a parse error
    javascript  `node --check` parses it without running it
    shell       `sh -n` parses it and exits non-zero on a syntax error
    mermaid     the vendored build draws it in a headless browser

None of them is a substitute for the other, which is the point of asking the
file what it is first. A `.php` that is really a PNG is a note, not a php run.

A missing binary is a note and never a pass. `php` absent from the machine means
the php files were not checked, and the report says that in the same list as
everything else, so a run with no errors and a skipped checker cannot be read as
a run where everything passed.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import mermaid
from .detect import detect_file, read_text
from .findings import Finding, Report, Severity

#: How long one external parser is allowed to take.
CHECK_TIMEOUT_SECONDS = 60

#: The most findings one checker reports for one file, so a broken generated
#: file cannot fill the output by itself.
FINDING_CAP = 12

#: Path fragments that mean a file is somebody else's build output. A vendored
#: bundle is not this repository's source and is not checked unless asked for.
VENDOR_MARKERS = ("/vendor/", "/node_modules/", "/site-packages/", "/.venv/", "/venv_")


@dataclass(frozen=True)
class Options:
    """What the caller wants run, and which browser or build to run it with."""

    render: bool = True
    chrome: str | None = None
    bundle: Path | None = None
    include_vendor: bool = False
    cap: int = FINDING_CAP


@dataclass(frozen=True)
class Context:
    """The path and name a finding is anchored to, plus the run's options."""

    path: Path
    name: str
    options: Options = field(default_factory=Options)


def _relative(path: Path, root: Path | None) -> str:
    if root is not None:
        try:
            return str(path.resolve().relative_to(root))
        except ValueError:
            pass
    return str(path)


def _missing_tool(context: Context, language: str, tool: str, package: str) -> Finding:
    return Finding(
        context.name,
        Severity.NOTE,
        f"{language}-not-checked",
        f"not checked: {tool} is not on the path",
        hint=f"install it ({package}) to have this file parsed",
    )


def _run(context: Context, command: list[str]) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(
            command, capture_output=True, timeout=CHECK_TIMEOUT_SECONDS, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def check_python(context: Context, text: str) -> list[Finding]:
    try:
        compile(text, context.name, "exec")
    except SyntaxError as error:
        return [
            Finding(
                context.name,
                Severity.ERROR,
                "python-syntax",
                f"{error.msg}",
                line=error.lineno,
                column=error.offset,
            )
        ]
    return []


def check_json(context: Context, text: str) -> list[Finding]:
    try:
        json.loads(text)
    except json.JSONDecodeError as error:
        return [
            Finding(
                context.name,
                Severity.ERROR,
                "json-syntax",
                error.msg,
                line=error.lineno,
                column=error.colno,
            )
        ]
    return []


def check_jsonl(context: Context, text: str) -> list[Finding]:
    findings: list[Finding] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            json.loads(line)
        except json.JSONDecodeError as error:
            findings.append(
                Finding(
                    context.name,
                    Severity.ERROR,
                    "jsonl-syntax",
                    f"line is not a json value: {error.msg}",
                    line=number,
                    column=error.colno,
                )
            )
            if len(findings) >= context.options.cap:
                break
    return findings


def check_php(context: Context, text: str) -> list[Finding]:
    tool = shutil.which("php")
    if tool is None:
        return [_missing_tool(context, "php", "php", "brew install php")]
    result = _run(context, [tool, "-l", str(context.path)])
    if result is None:
        return [
            Finding(
                context.name,
                Severity.NOTE,
                "php-not-checked",
                "php -l did not finish, so the file was not parsed",
            )
        ]
    if result.returncode == 0:
        return []
    output = (result.stderr or result.stdout).decode("utf-8", "replace").strip()
    first = output.splitlines()[0] if output else "php -l reported a parse error"
    line_match = re.search(r"on line (\d+)", output)
    message = re.sub(r"^PHP\s+\w+\s+error:\s*", "", first)
    message = message.split(" in ")[0].strip()
    return [
        Finding(
            context.name,
            Severity.ERROR,
            "php-syntax",
            message or first,
            line=int(line_match.group(1)) if line_match else None,
        )
    ]


def check_javascript(context: Context, text: str) -> list[Finding]:
    tool = shutil.which("node")
    if tool is None:
        return [_missing_tool(context, "javascript", "node", "brew install node")]
    result = _run(context, [tool, "--check", str(context.path)])
    if result is None:
        return [
            Finding(
                context.name,
                Severity.NOTE,
                "javascript-not-checked",
                "node --check did not finish, so the file was not parsed",
            )
        ]
    if result.returncode == 0:
        return []
    lines = (result.stderr or result.stdout).decode("utf-8", "replace").splitlines()
    # node prints the path with the line number, then the source line, then a
    # caret under the token it stopped at. The caret is the column, because the
    # source line is printed with its own indentation kept.
    line_number = None
    column = None
    message = "node --check reported a syntax error"
    if lines:
        location = re.search(r":(\d+)\s*$", lines[0])
        if location:
            line_number = int(location.group(1))
        if len(lines) > 2 and lines[2].strip().startswith("^"):
            column = lines[2].index("^") + 1
        for candidate in lines[1:]:
            if "Error" in candidate:
                message = candidate.strip()
                break
    return [
        Finding(
            context.name,
            Severity.ERROR,
            "javascript-syntax",
            message,
            line=line_number,
            column=column,
        )
    ]


def check_shell(context: Context, text: str) -> list[Finding]:
    tool = shutil.which("sh")
    if tool is None:
        return [_missing_tool(context, "shell", "sh", "any base system has sh")]
    result = _run(context, [tool, "-n", str(context.path)])
    if result is None:
        return [
            Finding(
                context.name,
                Severity.NOTE,
                "shell-not-checked",
                "sh -n did not finish, so the file was not parsed",
            )
        ]
    if result.returncode == 0:
        return []
    output = (result.stderr or result.stdout).decode("utf-8", "replace").strip()
    first = output.splitlines()[0] if output else "sh -n reported a syntax error"
    line_match = re.search(r"line (\d+)", first)
    return [
        Finding(
            context.name,
            Severity.ERROR,
            "shell-syntax",
            first.split(":", 2)[-1].strip() or first,
            line=int(line_match.group(1)) if line_match else None,
        )
    ]


def check_mermaid(context: Context, text: str) -> list[Finding]:
    findings = mermaid.lint_source(text, context.name)
    if not context.options.render:
        findings.append(
            Finding(
                context.name,
                Severity.NOTE,
                "mmd-render-skipped",
                "the diagram was not drawn: --no-render was asked for",
            )
        )
        return findings
    if any(finding.severity is Severity.ERROR for finding in findings):
        # The source already says the diagram cannot parse, and starting a
        # browser to be told the same thing would report one fault twice.
        findings.append(
            Finding(
                context.name,
                Severity.NOTE,
                "mmd-render-skipped",
                "the diagram was not drawn: the source alone already says it cannot parse",
            )
        )
        return findings
    findings.extend(
        mermaid.lint_render(
            text, context.name, context.options.chrome, context.options.bundle
        )
    )
    return findings


#: Language to the checker that answers for it. A language that is not here is
#: reported as unchecked rather than passed over.
TEXT_CHECKERS = {
    "python": check_python,
    "json": check_json,
    "jsonl": check_jsonl,
    "mermaid": check_mermaid,
}

PATH_CHECKERS = {
    "php": check_php,
    "javascript": check_javascript,
    "shell": check_shell,
}


def lint_text(text: str, language: str, name: str, options: Options | None = None) -> list[Finding]:
    """Parse a string as one language, with no file involved.

    This is the entry a caller uses to ask about code before writing it
    anywhere, which is the same question the agent asks of a candidate file.
    """
    options = options or Options()
    checker = TEXT_CHECKERS.get(language)
    if checker is None:
        return [
            Finding(
                name,
                Severity.NOTE,
                "no-checker",
                f"no syntax checker here for {language}, so this text was not parsed",
            )
        ]
    return checker(Context(Path(name), name, options), text)


def lint_path(path: Path, root: Path | None = None, options: Options | None = None) -> Report:
    """Detect one file, then run the checker that answers for what it is."""
    options = options or Options()
    path = Path(path)
    name = _relative(path, root)
    kind = detect_file(path)
    report = Report(name, kind.language, kind.evidence)

    if not path.is_file():
        report.add(Severity.ERROR, "missing", "there is no file at this path")
        return report
    if kind.is_binary:
        report.add(
            Severity.NOTE,
            "binary-not-checked",
            f"{kind.language} is not text, so nothing was parsed",
        )
        return report
    if not options.include_vendor and any(marker in f"/{name}" for marker in VENDOR_MARKERS):
        report.add(
            Severity.NOTE,
            "vendored-not-checked",
            "the file is somebody else's build output, which is not parsed here",
            hint="pass --include-vendor to parse it anyway",
        )
        return report

    text = read_text(path)
    if text is None:
        report.add(Severity.ERROR, "unreadable", "the file could not be read as text")
        return report

    checker = TEXT_CHECKERS.get(kind.language)
    if checker is not None:
        report.extend(checker(Context(path, name, options), text))
    elif kind.language in PATH_CHECKERS:
        report.extend(PATH_CHECKERS[kind.language](Context(path, name, options), text))
    else:
        report.add(
            Severity.NOTE,
            "no-checker",
            f"no syntax checker here for {kind.language}",
        )
    return report


def supported_languages() -> tuple[str, ...]:
    """Every language this package has a checker for."""
    return tuple(sorted({*TEXT_CHECKERS, *PATH_CHECKERS}))
