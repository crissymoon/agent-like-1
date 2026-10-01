"""Parse the files named, or the tracked ones, and report what will not load.

    python3 tools/lint/check.py mermaid-viewer/diagrams/overall-flow.mmd
    python3 tools/lint/check.py --tracked --fail-on warning
    python3 tools/lint/check.py --fix tools/lint/

The file's type is decided first, from its bytes and then from its suffix, and
the checker for that type runs. That order is the point of the command: a weight
file named `.json` is reported as a weight file and no json parser is handed it.

`--fix` makes the three mechanical edits and nothing else, and every one it makes
is printed with the lines it touched. Without it, the same edits are reported as
notes, so a run can say what it would have changed before changing it.

Exit status: zero when nothing reached the fail level, which is `error` unless
`--fail-on` says otherwise; one when something did; two when a path could not be
read at all.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
# The package is imported as `lint`, so the directory that holds it is what goes
# on the path rather than the one it is in.
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from lint import fixers  # noqa: E402
from lint.checkers import Options, lint_path  # noqa: E402
from lint.detect import detect_file, read_text  # noqa: E402
from lint.findings import Report, Severity  # noqa: E402
from lint.mermaid import bundle_path, find_chrome  # noqa: E402


def project_root() -> Path:
    """The checkout this command is running inside."""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], capture_output=True, check=False
    )
    if result.returncode == 0:
        return Path(result.stdout.decode().strip())
    return HERE.parent.parent


def tracked_files(root: Path) -> list[Path]:
    result = subprocess.run(["git", "ls-files", "-z"], cwd=str(root), capture_output=True, check=True)
    names = result.stdout.decode().split("\0")
    return [root / name for name in names if name]


def expand(argument: str, root: Path) -> list[Path]:
    path = Path(argument)
    if not path.is_absolute():
        path = Path.cwd() / path
    if path.is_dir():
        return sorted(child for child in path.rglob("*") if child.is_file())
    return [path]


#: Codes that mean the file was left alone on purpose: it is a weight file, it is
#: somebody else's build output, or it is not there. An edit is not planned for a
#: file the report has already declined to read, or a vendored bundle would be
#: rewritten by a whitespace rule while its checker was told to stay out of it.
SKIPPED_CODES = frozenset({"binary-not-checked", "vendored-not-checked", "missing"})


def _is_skipped(report: Report) -> bool:
    return any(finding.code in SKIPPED_CODES for finding in report.findings)


def _fix_notes(report: Report, text: str) -> None:
    """Report the mechanical edits the file is owed, without making them."""
    for code, lines in fixers.plan(text):
        report.add(
            Severity.NOTE,
            f"fix-available-{code}",
            f"line(s) {', '.join(str(line) for line in lines[:12])} would change",
            hint="run with --fix to make this edit",
        )


def _make_fixes(text: str, path: Path) -> list[str] | None:
    """Make the mechanical edits. Returns what was done, or None when nothing was."""
    rewritten, made = fixers.apply(text)
    if not made:
        return None
    path.write_text(rewritten, encoding="utf-8")
    return [
        f"{code}: line(s) {', '.join(str(line) for line in lines[:12])}" for code, lines in made
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="lint",
        description="Decide what each file is, then run the checker that answers for it.",
    )
    parser.add_argument("paths", nargs="*", help="files or directories, or nothing with --tracked")
    parser.add_argument("--tracked", action="store_true", help="every file git tracks")
    parser.add_argument("--fix", action="store_true", help="make the mechanical edits")
    parser.add_argument("--no-render", action="store_true", help="skip drawing mermaid diagrams")
    parser.add_argument("--chrome", help="the browser to draw diagrams with")
    parser.add_argument("--bundle", help="the mermaid build to draw diagrams with")
    parser.add_argument("--include-vendor", action="store_true", help="parse vendored builds too")
    parser.add_argument(
        "--fail-on",
        choices=[severity.value for severity in Severity],
        default=Severity.ERROR.value,
        help="the severity that decides the exit status",
    )
    parser.add_argument("--json", action="store_true", help="report as json")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="one line per file, including the files no checker here answers for",
    )
    parser.add_argument("--quiet", action="store_true", help="only files with findings, then the total")
    args = parser.parse_args(argv)

    root = project_root()
    if args.chrome and not Path(args.chrome).is_file():
        parser.error(f"--chrome names {args.chrome}, which is not a file")
    if args.bundle and not Path(args.bundle).is_file():
        parser.error(f"--bundle names {args.bundle}, which is not a file")
    if args.tracked:
        targets = tracked_files(root)
    elif args.paths:
        targets = [path for argument in args.paths for path in expand(argument, root)]
    else:
        parser.error("name at least one path, or pass --tracked")

    options = Options(
        render=not args.no_render,
        chrome=find_chrome(args.chrome),
        bundle=bundle_path(args.bundle),
        include_vendor=args.include_vendor,
    )

    reports: list[Report] = []
    unreadable = 0
    for path in targets:
        if not path.is_file():
            unreadable += 1
            print(f"{path}: there is no file at this path", file=sys.stderr)
            continue
        kind = detect_file(path)
        text = read_text(path) if not kind.is_binary else None
        report = lint_path(path, root=root, options=options)
        if text is None or _is_skipped(report):
            pass
        elif args.fix:
            done = _make_fixes(text, path)
            if done is not None:
                report = lint_path(path, root=root, options=options)
                report.fixes = done
        else:
            _fix_notes(report, text)
        reports.append(report)

    tally = {severity.value: 0 for severity in Severity}
    for report in reports:
        for severity, count in report.counts().items():
            tally[severity] += count

    fail_level = Severity(args.fail_on)
    failing = [report for report in reports if report.at_least(fail_level)]
    fix_total = sum(len(report.fixes) for report in reports)

    if args.json:
        print(
            json.dumps(
                {
                    "counts": tally,
                    "files": len(reports),
                    "failing": len(failing),
                    "fail_on": fail_level.value,
                    "reports": [report.to_dict() for report in reports],
                },
                indent=2,
            )
        )
    else:
        unchecked: dict[str, int] = {}
        for report in reports:
            hidden = [finding for finding in report.findings if finding.code == "no-checker"]
            if not args.verbose and hidden:
                # A language with no checker here is a fact about this command
                # rather than about the file, so it is counted by language once
                # at the end instead of printed once per file.
                report.findings = [
                    finding for finding in report.findings if finding.code != "no-checker"
                ]
                unchecked[report.language] = unchecked.get(report.language, 0) + 1
            if args.quiet and not report.findings and not report.fixes:
                continue
            for line in report.lines():
                print(line)
            for fix in report.fixes:
                print(f"{report.path}: fixed: {fix}")
        if args.quiet or unchecked:
            print()
        if unchecked:
            listing = ", ".join(
                f"{language} ({count})" for language, count in sorted(unchecked.items())
            )
            print(f"lint: no checker here for: {listing} - use --verbose to list those files")
        print(
            f"lint: {len(reports)} file(s), {tally['error']} error(s), "
            f"{tally['warning']} warning(s), {tally['note']} note(s), "
            f"{fix_total} fix(es), fail level {fail_level.value}"
        )

    if unreadable:
        return 2
    return 1 if failing else 0


if __name__ == "__main__":
    raise SystemExit(main())
