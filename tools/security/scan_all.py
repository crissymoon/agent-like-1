"""One reading of every surface, and one exit status for all of them.

    python3 tools/security/scan_all.py                       # the tracked tree, offline
    python3 tools/security/scan_all.py --fail-on medium      # stricter than the default
    python3 tools/security/scan_all.py --with-registry       # also ask the registry
    python3 tools/security/scan_all.py --json results/security/scan.json

The four readings answer different questions and none of them is a subset of
another:

    secrets   is a credential, a closed directory or a machine path in the tree
              or in any blob any ref can reach
    code      is a shape a defect is written in in the source, and does the
              application still have the properties it says it has
    files     what the tree holds and how it is held: modes, links, credential
              names, and the ignore rules that hold them out
    deps      what this tree installs, whether the versions are pinned, and
              whether the bytes are verified

They are run in one process and reported as one list, because a reader looking at
one of them should see the others rather than having to know they exist. The
secrets reading is taken by running `scan_secrets` as the command it already is,
rather than by a second implementation: the reading the release review takes and
the reading this takes are then the same code, and the same list of what a
credential is.

The default fail level is `high`, which is the level the release review uses. A
reading that could not be taken is `note`, never a pass, so a scan with no network
and a scan with nothing to find do not print the same thing.

Exit status: zero when nothing reached the fail level, one when something did, and
two when a reading could not be taken at all.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from findings import Finding, Report, Severity  # noqa: E402
from scan_code import scan as scan_code  # noqa: E402
from scan_deps import scan as scan_deps  # noqa: E402
from scan_files import scan as scan_files  # noqa: E402

#: The secret scanner, run as a command rather than imported. What it reports and
#: what a credential is stay defined in one place, and this check only decides how
#: a kind of finding reads in a combined report.
SECRET_SCANNER = HERE / "scan_secrets.py"

#: Which kind of secret finding is how serious. A credential is the one that has
#: to be rotated; a closed directory is a mistake that is undone by a delete; a
#: machine path is a location that travels with a record.
SECRET_SEVERITY = {
    "secret": Severity.HIGH,
    "path": Severity.MEDIUM,
    "disclosure": Severity.LOW,
}


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


def secrets_report(root: Path) -> Report:
    """The secret scanner's tree view and history view, read through its own command."""
    report = Report(name="scan_secrets", surface="tracked and history")
    with tempfile.TemporaryDirectory(prefix="security-scan-") as scratch:
        for mode in ("tracked", "history"):
            destination = Path(scratch) / f"{mode}.json"
            result = subprocess.run(
                [
                    sys.executable,
                    str(SECRET_SCANNER),
                    f"--{mode}",
                    "--paths",
                    "--json",
                    str(destination),
                    "--quiet",
                ],
                cwd=str(root),
                capture_output=True,
                check=False,
            )
            if result.returncode == 2:
                raise RuntimeError(
                    "the secret scanner could not run: "
                    + result.stderr.decode("utf-8", errors="replace").strip()
                )
            try:
                payload = json.loads(destination.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raise RuntimeError(f"the secret scanner wrote no reading for {mode}")
            for finding in payload.get("findings", []):
                kind = str(finding.get("kind", ""))
                report.add(
                    f"secret-{mode}-{finding.get('name', kind)}",
                    SECRET_SEVERITY.get(kind, Severity.NOTE),
                    str(finding.get("path", "")),
                    str(finding.get("detail", "")),
                    line=int(finding.get("line", 0) or 0),
                    excerpt=str(finding.get("excerpt", "")),
                )
            report.tally(f"readings-{mode}", 1)
            report.tally(f"{mode}-findings", len(payload.get("findings", [])))

    return report


def scan(root: Path, with_registry: bool = False) -> list[Report]:
    """Every surface, in the order a reader wants them: what is here, then what it installs."""
    return [
        secrets_report(root),
        scan_code(root),
        scan_files(root),
        scan_deps(root, with_registry=with_registry),
    ]


def merge(reports: list[Report]) -> Report:
    """One report of reports, with the surface a finding came from kept in its code."""
    combined = Report(name="security", surface="tracked tree, history, source, files, dependencies")
    for report in reports:
        for finding in report.findings:
            combined.findings.append(
                Finding(
                    code=finding.code,
                    severity=finding.severity,
                    path=finding.path,
                    message=finding.message,
                    line=finding.line,
                    hint=finding.hint,
                    excerpt=finding.excerpt,
                )
            )
        for key, value in report.counted.items():
            combined.tally(f"{report.name}:{key}", value)

    return combined


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scan_all",
        description="Read every security surface of this checkout and report one list.",
    )
    parser.add_argument(
        "--fail-on",
        choices=[severity.value for severity in Severity],
        default=Severity.HIGH.value,
        help="the severity that decides the exit status",
    )
    parser.add_argument(
        "--with-registry",
        action="store_true",
        help="also ask the registry for advisories, which needs a network",
    )
    parser.add_argument("--json", metavar="FILE", help="write the whole reading to a file as JSON")
    parser.add_argument("--quiet", action="store_true", help="the surfaces and the totals, nothing else")
    args = parser.parse_args(argv)

    try:
        root = repository_root()
        reports = scan(root, with_registry=args.with_registry)
    except (RuntimeError, OSError) as error:
        print(f"scan_all: {error}", file=sys.stderr)
        return 2

    combined = merge(reports)
    level = Severity(args.fail_on)
    failing = combined.at(level)

    if not args.quiet:
        for report in reports:
            print(report.summary())
        print()
        for finding in combined.at(Severity.NOTE):
            print(finding.render())
        print()

    print(f"security: {len(combined.findings)} finding(s) across {len(reports)} surface(s)")
    print(f"security: {len(failing)} at or above {level.value}")
    for finding in failing:
        print("  " + finding.render().replace("\n", "\n  "))

    if args.json:
        destination = Path(args.json)
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = combined.as_document()
        payload["surfaces"] = [report.as_document() for report in reports]
        destination.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"security: wrote {destination}")

    return 1 if failing else 0


if __name__ == "__main__":
    raise SystemExit(main())
