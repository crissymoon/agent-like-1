"""Read what this tree depends on, and what it is allowed to fetch.

    python3 tools/security/scan_deps.py
    python3 tools/security/scan_deps.py --with-registry
    python3 tools/security/scan_deps.py --json results/security/deps.json

A dependency is a decision that outlives the commit that made it. What this reads
is the part of that decision the tree can answer by itself:

  * whether a declared range names a version the lock file resolves, or a version
    that moves: a wildcard, a `latest`, a branch, or an archive fetched over plain
    http rather than from the registry;
  * whether every locked package carries an integrity hash, because a lock without
    one is a lock that verifies nothing about the bytes it installs;
  * whether a lock file is in the tree at all, since a manifest alone installs
    whatever is current on the machine that runs it;
  * whether a Python manifest pins what it installs, on the same grounds.

Advisories are read only when `--with-registry` is asked for, and the reason is
that the answer depends on a network and a registry state rather than on this
commit. Reading it by default would make a reading of the tree depend on the day
it was taken. When it is asked for, a registry that cannot be reached is reported
as a reading that was not taken, never as a scan that passed.

Exit status follows the same rule as the rest of the package.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from findings import Report, Severity  # noqa: E402

#: The npm project, which is the one dependency set this repository has.
NPM_PROJECT = "desktop"
NPM_MANIFEST = "package.json"
NPM_LOCK = "package-lock.json"

#: Python manifests, of which this tree currently holds none. Named rather than
#: guessed, so a reader can see what would be read.
PYTHON_MANIFESTS: tuple[str, ...] = ("requirements.txt", "requirements-dev.txt", "pyproject.toml", "Pipfile", "environment.yml")

#: A range that names no version: whatever is current when the install runs.
MOVING_RANGES = ("*", "latest", "", "x")

#: How long the registry is given, and how many entries are read from it.
REGISTRY_TIMEOUT_SECONDS = 120

#: Registry severity to what it means here. A critical advisory stops a push.
REGISTRY_SEVERITY = {
    "critical": Severity.HIGH,
    "high": Severity.HIGH,
    "moderate": Severity.MEDIUM,
    "low": Severity.LOW,
    "info": Severity.NOTE,
}

#: Advisories that have been read and cannot be acted on yet, with the reason.
#:
#: An exception here is not a suppression: the advisory is reported as a reading
#: every time, with this reason beside it, and it is counted in the summary. The
#: test for holding one is that the remedy does not exist rather than that the
#: upgrade is inconvenient - an advisory with a fixed version published is a
#: finding, and the finding says which version to move to.
REGISTRY_EXCEPTIONS: dict[str, str] = {
    "GHSA-ch52-4w7c-c8xp": (
        "every published version of http-cache-semantics is at or below the affected range "
        "(<=4.2.0, and 4.2.0 is the newest), so there is no version to move to; the chain is "
        "build toolchain rather than shipped code - @electron/get reaches it through got, and "
        "got is only used to download the Electron archive while packaging"
    ),
}


def _advisory_identifier(*values: object) -> str:
    """The advisory id inside whatever a field holds, which is usually a url."""
    for value in values:
        if not isinstance(value, str):
            continue
        for token in value.replace("/", " ").split():
            if token.startswith("GHSA-"):
                return token

    return ""


def _own_advisories(entry: dict) -> list[str]:
    """The advisory identifiers a package is flagged for in its own right."""
    flagged = entry.get("via")
    if not isinstance(flagged, list):
        return []

    return [
        identifier
        for item in flagged
        if isinstance(item, dict)
        for identifier in [_advisory_identifier(item.get("url", ""), item.get("source", ""))]
        if identifier
    ]


def _exception_closure(vulnerabilities: dict) -> set[str]:
    """Every package that is flagged only because of an already excepted advisory.

    npm reports one advisory against every package above it, so a single
    unfixable advisory deep in the graph arrives as a finding for each package
    that reaches it. Asked naively, that is one advisory reported eight times,
    which reads the same as eight problems. So the graph is closed over:

      * a package is resolved when every advisory it is flagged for in its own
        right is an excepted one, and every package it is flagged through is
        either resolved or carries no advisory of its own;
      * the closure is repeated until it stops changing, which is what makes a
        cycle of packages that carry no advisory of their own resolve rather than
        block itself. npm reports a sibling relationship as a two way reference,
        and a cycle is not a finding.

    What this cannot hide is an advisory that is not on the list: an advisory of
    its own keeps a package unresolved however many neighbours resolve, and that
    package is then reported with the version that clears it.
    """
    flagged = {
        name: entry for name, entry in vulnerabilities.items() if isinstance(entry, dict)
    }
    with_own_advisory = {name for name, entry in flagged.items() if _own_advisories(entry)}
    unresolved_advisories = {
        name: [
            identifier
            for identifier in _own_advisories(entry)
            if identifier not in REGISTRY_EXCEPTIONS
        ]
        for name, entry in flagged.items()
    }

    resolved: set[str] = set()
    changed = True
    while changed:
        changed = False
        for name, entry in flagged.items():
            if name in resolved:
                continue
            blocked = bool(unresolved_advisories.get(name))
            if not blocked:
                for item in entry.get("via") or []:
                    if isinstance(item, dict):
                        continue
                    referenced = str(item)
                    if referenced in resolved:
                        continue
                    if referenced in with_own_advisory:
                        blocked = True
                        break
            if blocked:
                continue
            resolved.add(name)
            changed = True

    return resolved


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


def read_json(path: Path) -> dict | None:
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    return decoded if isinstance(decoded, dict) else None


def check_range(name: str, spec: str, path: str, report: Report) -> None:
    """One declared dependency, held against what a range is allowed to be."""
    if not isinstance(spec, str):
        report.add("dependency-not-text", Severity.MEDIUM, path, f"{name} is declared as something other than a range")
        return
    lowered = spec.strip()
    if lowered.startswith(("git+", "github:", "http://", "https://", "file:", "link:")):
        report.add(
            "dependency-not-from-registry",
            Severity.HIGH,
            path,
            f"{name} is fetched from {lowered.split(':', 1)[0]} rather than from the registry",
            hint="a git branch or an archive is a version nobody pins, and an http one is also unverified",
        )
        return
    if lowered.startswith("ssh:"):
        report.add("dependency-not-from-registry", Severity.HIGH, path, f"{name} is fetched over ssh")
        return
    if lowered in MOVING_RANGES:
        report.add(
            "dependency-moves",
            Severity.MEDIUM,
            path,
            f"{name} is declared as {lowered or 'an empty range'}",
            hint="a wildcard resolves to whatever is current, which is not a decision this commit made",
        )
        return
    if lowered.startswith((">=", ">", "^", "~")) or lowered.startswith("*"):
        report.add(
            "dependency-open-range",
            Severity.LOW,
            path,
            f"{name} is declared as {lowered}",
            hint="the lock file is what pins it; a range with no upper bound is the reason the lock has to exist",
        )
        return
    report.tally("dependencies-pinned")


def scan_npm(root: Path, report: Report) -> None:
    """The manifest's declared dependencies and the lock's pinned ones."""
    project = root / NPM_PROJECT
    manifest_path = project / NPM_MANIFEST
    if not manifest_path.is_file():
        report.add(
            "npm-manifest-absent",
            Severity.NOTE,
            str(manifest_path.relative_to(root)),
            "no npm manifest was found, so no dependency was read",
        )
        return

    manifest = read_json(manifest_path)
    if manifest is None:
        report.add("npm-manifest-unreadable", Severity.HIGH, str(manifest_path.relative_to(root)), "the manifest could not be parsed")
        return

    declared: list[tuple[str, str, str]] = []
    for section, severity_hint in (("dependencies", "ships"), ("devDependencies", "build only")):
        block = manifest.get(section)
        if not isinstance(block, dict):
            continue
        report.tally(f"npm-{section}", len(block))
        for name, spec in sorted(block.items()):
            declared.append((name, spec, section))
    if not declared:
        report.add(
            "npm-no-dependencies",
            Severity.NOTE,
            str(manifest_path.relative_to(root)),
            "the manifest declares no dependency at all",
        )

    lock_path = project / NPM_LOCK
    lock = read_json(lock_path)
    if lock is None:
        for name, spec, section in declared:
            report.add(
                "dependency-unlocked",
                Severity.MEDIUM,
                str(manifest_path.relative_to(root)),
                f"{name} is declared in {section} and there is no lock file to pin it",
                hint="every install on every machine then resolves the range again",
            )
        return

    relative_lock = str(lock_path.relative_to(root))
    version = lock.get("lockfileVersion")
    if not isinstance(version, int) or version < 2:
        report.add(
            "lockfile-old",
            Severity.LOW,
            relative_lock,
            f"the lock file is version {version!r}",
            hint="version 2 and later records the whole install graph, which is what makes an integrity hash possible for every package",
        )
    report.tally("lock-files", 1)

    packages = lock.get("packages")
    if not isinstance(packages, dict):
        report.add(
            "lockfile-no-packages",
            Severity.MEDIUM,
            relative_lock,
            "the lock file records no package set",
        )
        return

    for name, spec, section in declared:
        check_range(name, spec, relative_lock, report)
        entry = packages.get(f"node_modules/{name}")
        if not isinstance(entry, dict):
            report.add(
                "dependency-not-locked",
                Severity.MEDIUM,
                relative_lock,
                f"{name} is declared in {section} and is not in the lock file",
                hint="the install would resolve it from the range at install time",
            )
            continue
        resolved = entry.get("resolved")
        integrity = entry.get("integrity")
        if resolved is None and entry.get("link") is True:
            report.tally("locked-links")
            continue
        if not isinstance(integrity, str) or not integrity.startswith("sha512-"):
            report.add(
                "lockentry-no-integrity",
                Severity.MEDIUM,
                relative_lock,
                f"{name} is locked without a sha512 integrity hash",
                hint="the lock then records a version without verifying the bytes that version resolves to",
            )
            continue
        report.tally("locked-with-integrity")

    for name, entry in packages.items():
        if name == "" or not isinstance(entry, dict):
            continue
        if entry.get("resolved") is None:
            continue
        integrity = entry.get("integrity")
        if not isinstance(integrity, str) or not integrity.startswith("sha512-"):
            report.tally("packages-without-integrity")


def scan_python(root: Path, report: Report) -> None:
    """Python manifests, which this tree holds none of, read the way it would read them."""
    found = [name for name in PYTHON_MANIFESTS if (root / name).is_file()]
    if not found:
        report.add(
            "python-manifest-absent",
            Severity.NOTE,
            ".",
            "no Python manifest is tracked, so no Python dependency was read",
            hint="the environments this repository uses are produced by its own installer rather than by pip install -r",
        )
        return
    for name in found:
        try:
            text = (root / name).read_text(encoding="utf-8")
        except OSError:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped == "" or stripped.startswith("#"):
                continue
            if "--index-url http://" in stripped or "-i http://" in stripped:
                report.add(
                    "python-index-insecure",
                    Severity.HIGH,
                    name,
                    "packages are fetched over plain http",
                    line=number,
                    excerpt=stripped,
                )
                continue
            if stripped.startswith(("-e ", "--editable ")) or "git+" in stripped:
                report.add(
                    "python-dependency-not-from-index",
                    Severity.MEDIUM,
                    name,
                    "a dependency is fetched from a repository rather than from an index",
                    line=number,
                    excerpt=stripped,
                )
                continue
            if any(marker in stripped for marker in ("==", "===", " @ ")):
                report.tally("python-pins")
                continue
            report.add(
                "python-dependency-unpinned",
                Severity.LOW,
                name,
                "a dependency names no exact version",
                line=number,
                excerpt=stripped,
            )


def scan_registry(root: Path, report: Report) -> None:
    """Ask the registry for advisories, and say so when it cannot be asked."""
    project = root / NPM_PROJECT
    if not (project / NPM_LOCK).is_file():
        report.add(
            "registry-not-asked",
            Severity.NOTE,
            str(project.relative_to(root)),
            "no lock file to audit, so advisories were not read",
        )
        return
    npm = shutil.which("npm")
    if npm is None:
        report.add(
            "registry-not-asked",
            Severity.NOTE,
            NPM_PROJECT,
            "npm is not on the path, so advisories were not read",
        )
        return
    try:
        result = subprocess.run(
            [npm, "audit", "--json", "--package-lock-only"],
            cwd=str(project),
            capture_output=True,
            timeout=REGISTRY_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        report.add(
            "registry-unreachable",
            Severity.NOTE,
            NPM_PROJECT,
            f"advisories were not read: {type(error).__name__}",
            hint="this is a reading that was not taken, which is not the same as a reading that was clean",
        )
        return
    payload = result.stdout.decode("utf-8", errors="replace")
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError:
        report.add(
            "registry-unreadable",
            Severity.NOTE,
            NPM_PROJECT,
            "the audit answered in a form this check does not read",
            hint=payload.strip().splitlines()[0][:160] if payload.strip() else "no output",
        )
        return
    if not isinstance(decoded, dict):
        report.add("registry-unreadable", Severity.NOTE, NPM_PROJECT, "the audit answered with something else")
        return
    vulnerabilities = decoded.get("vulnerabilities")
    if not isinstance(vulnerabilities, dict):
        report.add(
            "registry-no-advisories",
            Severity.NOTE,
            NPM_PROJECT,
            "the audit reported no advisory",
            hint=f"npm exited {result.returncode}",
        )
        return
    report.tally("registry-packages-read", len(vulnerabilities))
    closure = _exception_closure(vulnerabilities)
    for name, entry in sorted(vulnerabilities.items()):
        if not isinstance(entry, dict):
            continue
        advisories = [item for item in entry.get("via", []) if isinstance(item, dict)]
        severity = REGISTRY_SEVERITY.get(str(entry.get("severity", "info")).lower(), Severity.NOTE)
        own = _own_advisories(entry)
        identifier = own[0] if own else ""
        title = advisories[0].get("title") if advisories else "an advisory"
        if identifier and identifier in REGISTRY_EXCEPTIONS:
            report.add(
                "registry-exception",
                Severity.NOTE,
                NPM_PROJECT,
                f"{name}: {title} ({identifier})",
                hint=REGISTRY_EXCEPTIONS[identifier],
            )
            report.tally("registry-exceptions", 1)
            continue
        if name in closure:
            report.add(
                "registry-exception-chain",
                Severity.NOTE,
                NPM_PROJECT,
                f"{name}: flagged only because of an advisory below it in the chain",
                hint="the advisory itself is reported beside this, with the reason it cannot be acted on yet",
            )
            report.tally("registry-exception-chains", 1)
            continue
        fix = entry.get("fixAvailable")
        if isinstance(fix, dict):
            remedy = f"upgrade {fix.get('name')} to {fix.get('version')}"
        elif fix is True:
            remedy = "an upgrade inside the declared range clears it: npm audit fix"
        else:
            remedy = "no fixed version is published, which is what an exception is for"
        report.add(
            "registry-advisory",
            severity,
            NPM_PROJECT,
            f"{name}: {title}{f' ({identifier})' if identifier else ''}",
            hint=f"severity {entry.get('severity')}; {remedy}",
        )


def scan(root: Path, with_registry: bool = False) -> Report:
    """Every reading this scanner takes over the dependency set."""
    report = Report(name="scan_deps", surface="tracked")
    scan_npm(root, report)
    scan_python(root, report)
    if with_registry:
        scan_registry(root, report)
    else:
        report.add(
            "registry-not-asked",
            Severity.NOTE,
            ".",
            "advisories were not read, because reading them depends on the registry today rather than on this commit",
            hint="pass --with-registry to ask",
        )

    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scan_deps",
        description="Read the dependency set for versions that move and packages that verify nothing.",
    )
    parser.add_argument(
        "--with-registry",
        action="store_true",
        help="also ask the registry for advisories, which needs a network",
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
    except RuntimeError as error:
        print(f"scan_deps: {error}", file=sys.stderr)
        return 2

    report = scan(root, with_registry=args.with_registry)
    level = Severity(args.fail_on)
    failing = report.at(level)
    if not args.quiet:
        for finding in report.at(Severity.NOTE):
            print(finding.render())
    print(report.summary())
    print(f"scan_deps: {len(failing)} finding(s) at or above {level.value}")

    if args.json:
        destination = Path(args.json)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(report.to_json() + "\n", encoding="utf-8")

    return 1 if failing else 0


if __name__ == "__main__":
    raise SystemExit(main())
