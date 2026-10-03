"""Read the source for the shapes a defect is written in, and for the ones it is not.

    python3 tools/security/scan_code.py --tracked
    python3 tools/security/scan_code.py desktop/main.js --fail-on medium
    python3 tools/security/scan_code.py --tracked --json

Three readings are taken here and they are different kinds of thing:

  * the construct rules, which read line by line for a shape that is a defect as
    read (a command built from a request, a string handed to a shell) or a place
    that is safe only because of something nearby;
  * the document checks, which read a whole page, because whether a page runs
    script of its own and declares a policy is a question about the file;
  * the application properties, which are the ones a reader cannot see on screen:
    context isolation, the sandbox, the content security policy and the guards in
    front of the bridge. Each is a thing the code says it has, and a property
    that stops being true is not visible in a diff.

What this cannot see is reachability. A pattern that matches a line does not know
whether the line runs, and one that misses does not know what a value is. So the
report says what it read and what it skipped, and an exception is written down in
the source with `security-allow: <reason>` rather than left to a change in a rule.

Exit status: zero when nothing reached the fail level, one when something did, and
two when the reading could not be taken.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from findings import Finding, Report, Severity  # noqa: E402
from rules import (  # noqa: E402
    ALLOW_FILE_MARKER,
    ALLOW_MARKER,
    DOCUMENT_SUFFIXES,
    ELECTRON_ASSERTIONS,
    BRIDGE_ASSERTIONS,
    INLINE_SCRIPT,
    LANGUAGES,
    POLICY_META,
    POLICY_UNSAFE_INLINE,
    SHELL_STRICTNESS,
    VENDOR_MARKERS,
    rules_for,
)

#: Files larger than this are not read. A source file is a page of text, and a
#: file past this size is a build product that got committed.
MAX_FILE_BYTES = 2 * 1024 * 1024

#: Findings per file, so one generated file cannot fill the report by itself.
FINDING_CAP = 25

#: How much of a line an excerpt carries.
EXCERPT_CHARS = 160


def repository_root(start: Path | None = None) -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=str(start or HERE),
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("not inside a git working tree")

    return Path(result.stdout.decode("utf-8").strip())


def tracked_paths(root: Path) -> list[str]:
    result = subprocess.run(["git", "ls-files"], cwd=str(root), capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError("git ls-files could not run")

    return [line for line in result.stdout.decode("utf-8").splitlines() if line.strip()]


def language_of(relative: str) -> str | None:
    suffix = Path(relative).suffix.lower()
    for language, suffixes in LANGUAGES.items():
        if suffix in suffixes:
            return language

    return None


def is_vendored(relative: str) -> bool:
    marked = f"/{relative}"

    return any(marker in marked for marker in VENDOR_MARKERS)


def read_text(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if len(data) > MAX_FILE_BYTES or b"\x00" in data[:4096]:
        return None

    return data.decode("utf-8", errors="replace")


def _excerpt(line: str) -> str:
    text = line.strip()
    if len(text) <= EXCERPT_CHARS:
        return text
    return text[: EXCERPT_CHARS - 1] + "\u2026"


def scan_lines(text: str, relative: str, language: str, report: Report) -> None:
    """Every construct rule, line by line, with the exceptions counted."""
    allowed_file = ALLOW_FILE_MARKER in text
    if allowed_file:
        report.tally("files-allowed")
    reported = 0
    for number, line in enumerate(text.splitlines(), start=1):
        if ALLOW_MARKER in line:
            report.tally("lines-allowed")
            continue
        if allowed_file:
            continue
        for rule in rules_for(language):
            match = rule.pattern.search(line)
            if match is None:
                continue
            if rule.also and not all(marker in line for marker in rule.also):
                continue
            if rule.unless and any(marker in line for marker in rule.unless):
                continue
            if rule.unless_re is not None and rule.unless_re.search(line):
                continue
            if reported >= FINDING_CAP:
                report.tally("lines-not-reported")
                return
            report.add(
                rule.code,
                rule.severity,
                relative,
                rule.message,
                line=number,
                hint=rule.hint,
                excerpt=_excerpt(line),
            )
            reported += 1


def scan_document(text: str, relative: str, report: Report) -> None:
    """A page, read for script it runs itself and the policy it declares."""
    if INLINE_SCRIPT.search(text) is None:
        return
    declares = POLICY_META.search(text) is not None
    if not declares:
        report.add(
            "document-inline-script",
            Severity.MEDIUM,
            relative,
            "the page runs script of its own and declares no policy",
            hint=(
                "a meta http-equiv Content-Security-Policy with default-src 'none' and a "
                "script-src that names what the page loads; without one, any text that reaches "
                "the page as markup runs with the page's own privileges"
            ),
        )
        return
    if POLICY_UNSAFE_INLINE.search(text) is not None:
        report.add(
            "document-unsafe-inline",
            Severity.MEDIUM,
            relative,
            "the policy declares a policy and allows inline script",
            hint="an inline allowance is the policy declining to answer for the script it did not write",
        )


def scan_assertions(root: Path, report: Report) -> None:
    """The properties of the Electron application, read from the three files that hold them."""
    texts: dict[str, str] = {}
    for assertion in (*ELECTRON_ASSERTIONS, *BRIDGE_ASSERTIONS):
        if assertion.path in texts:
            continue
        text = read_text(root / assertion.path)
        texts[assertion.path] = text if text is not None else ""
        if text is None:
            report.add(
                "file-not-read",
                Severity.NOTE,
                assertion.path,
                "the file these properties live in was not read",
                hint="it is missing, binary, or larger than the read limit",
            )

    for assertion in (*ELECTRON_ASSERTIONS, *BRIDGE_ASSERTIONS):
        if assertion.satisfied(texts.get(assertion.path, "")):
            continue
        report.add(
            assertion.code,
            assertion.severity,
            assertion.path,
            assertion.message,
            hint=assertion.hint,
        )


def scan_shell_strictness(text: str, relative: str, report: Report) -> None:
    """Whether a script stops at the first failure, counted rather than reported as a defect."""
    if SHELL_STRICTNESS in text:
        report.tally("scripts-strict")
        return
    report.tally("scripts-without-set-e")
    report.add(
        "shell-no-set-e",
        Severity.NOTE,
        relative,
        "the script does not set -e, so a failed step is not the end of it",
        hint="a reading rather than a defect: some scripts check their commands deliberately",
    )


def scan(root: Path, targets: list[str] | None = None) -> Report:
    """Every reading this scanner takes over the named paths, or the tracked ones."""
    report = Report(name="scan_code", surface="tracked" if targets is None else "named")
    paths = tracked_paths(root) if targets is None else list(targets)
    for relative in paths:
        absolute = root / relative
        if not absolute.is_file():
            continue
        if is_vendored(relative):
            report.tally("files-vendored")
            continue
        suffix = absolute.suffix.lower()
        if suffix in DOCUMENT_SUFFIXES:
            text = read_text(absolute)
            if text is not None:
                report.tally("documents-read")
                scan_document(text, relative, report)
            continue
        language = language_of(relative)
        if language is None:
            continue
        text = read_text(absolute)
        if text is None:
            report.tally("files-skipped")
            continue
        report.tally(f"files-{language}")
        scan_lines(text, relative, language, report)
        if language == "shell":
            scan_shell_strictness(text, relative, report)

    scan_assertions(root, report)

    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scan_code",
        description="Read the source for the shapes a security defect is written in.",
    )
    parser.add_argument("paths", nargs="*", help="files to read; without them the tracked tree is read")
    parser.add_argument(
        "--tracked",
        action="store_true",
        help="read every file git tracks, which is what happens when no path is named",
    )
    parser.add_argument(
        "--fail-on",
        choices=[severity.value for severity in Severity],
        default=Severity.HIGH.value,
        help="the severity that decides the exit status",
    )
    parser.add_argument("--json", metavar="FILE", help="write the report to a file as JSON")
    parser.add_argument("--quiet", action="store_true", help="only the summary line")
    args = parser.parse_args(argv)

    try:
        root = repository_root()
        report = scan(root, targets=args.paths or None)
    except (RuntimeError, OSError) as error:
        print(f"scan_code: {error}", file=sys.stderr)
        return 2

    level = Severity(args.fail_on)
    failing = report.at(level)
    if not args.quiet:
        for finding in report.at(Severity.NOTE):
            print(finding.render())
    print(report.summary())
    print(f"scan_code: {len(failing)} finding(s) at or above {level.value}")

    if args.json:
        destination = Path(args.json)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(report.to_json() + "\n", encoding="utf-8")

    return 1 if failing else 0


if __name__ == "__main__":
    raise SystemExit(main())
