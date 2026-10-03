"""Read what the tree holds and how it is held: modes, names and the ignore rules.

    python3 tools/security/scan_files.py
    python3 tools/security/scan_files.py --json results/security/files.json

The source scanner reads what a file says. This one reads what a file is, which
is the half a text reading cannot see:

  * the mode of every tracked file, read from the index rather than from the
    working tree, because the index is what a push sends. An executable bit on a
    file that is not a script is a mode somebody set by accident, and a symlink in
    a commit is a path that resolves somewhere other than where it appears to;
  * the mode on disk, because the index does not carry a setuid bit and the
    filesystem does. A world writable file in a checkout is a file anybody on the
    machine may rewrite between two runs;
  * the names every credential is written as. The secret scanner reads content,
    and a key that is tracked by its name is a key that is tracked whether or not
    its content looks like one;
  * the ignore rules that hold those names out. A rule that lives in one file can
    be walked past by one command, so the rule that is missing is reported here
    as well as enforced by the pre-push check.

Exit status follows the same rule as every other check here: zero when nothing
reached the fail level, one when something did, two when the reading could not be
taken.
"""

from __future__ import annotations

import argparse
import re
import stat
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from findings import Report, Severity  # noqa: E402

#: Suffixes whose executable bit is expected: the scripts this repository runs.
SCRIPT_SUFFIXES = (".sh", ".py", ".php", ".command", ".bash")

#: Suffixes and names a credential is written as. The rule is by name, because a
#: key's content is what the secret scanner reads and its name is what a commit
#: adds.
CREDENTIAL_NAMES: tuple[str, ...] = (
    ".env",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "id_rsa",
    "id_ed25519",
    "credentials.json",
)
CREDENTIAL_SUFFIXES: tuple[str, ...] = (
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".p8",
    ".jks",
    ".keystore",
    ".mobileprovision",
    ".provisionprofile",
    ".der",
    ".cer",
)

#: Names that are templates rather than credentials, and are meant to be tracked.
CREDENTIAL_EXEMPT: tuple[str, ...] = (".env.example", ".env.sample", ".env.template", "service-account.example.json")

#: The ignore rules a checkout is expected to state, and what each one holds out.
EXPECTED_IGNORES: tuple[tuple[str, str], ...] = (
    (".env", "a dotenv file, which is where a key gets written when the environment is inconvenient"),
    ("*.pem", "a certificate or a key in its most common form"),
    ("*.key", "a private key"),
    ("id_rsa", "an ssh key"),
    ("*.p12", "a signing identity, which is a private key with a public half"),
    (".netrc", "a file of credentials for one host, read by curl without being asked"),
)

#: A mode git stores, and what it means.
GIT_MODES = {"100644": "file", "100755": "executable", "120000": "symlink", "160000": "submodule"}


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


def index_entries(root: Path) -> list[tuple[str, str, str]]:
    """Every tracked path with the mode git stores and the object it points at."""
    result = subprocess.run(["git", "ls-files", "-s", "-z"], cwd=str(root), capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError("git ls-files could not run")
    entries: list[tuple[str, str, str]] = []
    for record in result.stdout.decode("utf-8", errors="replace").split("\0"):
        if not record:
            continue
        header, _, path = record.partition("\t")
        fields = header.split()
        if len(fields) < 3 or not path:
            continue
        entries.append((fields[0], fields[1], path))

    return entries


def is_script(path: str) -> bool:
    return Path(path).suffix.lower() in SCRIPT_SUFFIXES


def looks_like_credential(path: str) -> bool:
    name = Path(path).name
    if name in CREDENTIAL_EXEMPT:
        return False
    if name in CREDENTIAL_NAMES:
        return True
    return Path(path).suffix.lower() in CREDENTIAL_SUFFIXES


def symlink_target(root: Path, object_id: str) -> str:
    result = subprocess.run(
        ["git", "cat-file", "blob", object_id], cwd=str(root), capture_output=True, check=False
    )
    if result.returncode != 0:
        return ""

    return result.stdout.decode("utf-8", errors="replace").strip()


def ignore_rules(root: Path) -> str:
    """The ignore file's rules, with the comment lines kept out of the comparison."""
    try:
        text = (root / ".gitignore").read_text(encoding="utf-8")
    except OSError:
        return ""

    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def mode_findings(root: Path, entries: list[tuple[str, str, str]], report: Report) -> None:
    """The executable bits, the links and the modes on disk."""
    for mode, object_id, path in entries:
        kind = GIT_MODES.get(mode, f"mode {mode}")
        if kind == "symlink":
            report.add(
                "tracked-symlink",
                Severity.NOTE,
                path,
                "a link is committed, so the path resolves to wherever it points on the machine that reads it",
                excerpt=symlink_target(root, object_id),
                hint="a link that points outside the tree is a link whose target is not in the commit",
            )
            continue
        if kind == "submodule":
            report.add(
                "tracked-submodule",
                Severity.NOTE,
                path,
                "a submodule is committed, so what it holds depends on where it is fetched from",
            )
            continue
        if kind == "executable" and not is_script(path):
            report.add(
                "unexpected-executable",
                Severity.LOW,
                path,
                "the executable bit is set on a file that is not a script",
                hint="git stores the mode, so it travels into every checkout",
            )

        absolute = root / path
        try:
            on_disk = absolute.lstat().st_mode
        except OSError:
            report.tally("files-not-on-disk")
            continue
        if on_disk & stat.S_ISUID:
            report.add(
                "setuid-file",
                Severity.HIGH,
                path,
                "the file is setuid, so it runs as its owner rather than as the caller",
            )
        if on_disk & stat.S_ISGID:
            report.add(
                "setgid-file",
                Severity.MEDIUM,
                path,
                "the file is setgid, so it runs with the group of the file rather than of the caller",
            )
        if on_disk & stat.S_IWOTH:
            report.add(
                "world-writable-file",
                Severity.HIGH,
                path,
                "anybody on this machine may rewrite the file",
            )
        elif on_disk & stat.S_IWGRP:
            report.tally("group-writable-files")


def name_findings(entries: list[tuple[str, str, str]], report: Report) -> None:
    """The files a credential is written as, tracked in a commit."""
    for _, _, path in entries:
        if not looks_like_credential(path):
            continue
        report.add(
            "tracked-credential-name",
            Severity.HIGH,
            path,
            "a file a credential is written as is tracked",
            hint="rotate what it holds, then remove it from the index; a commit is not undone by a later commit",
        )


def ignore_findings(root: Path, report: Report) -> None:
    """Whether the ignore file holds out the names a credential is written as."""
    rules = ignore_rules(root)
    if rules == "":
        report.add(
            "ignore-file-missing",
            Severity.HIGH,
            ".gitignore",
            "there is no ignore file, so nothing is held out by rule",
        )
        return
    patterns = {line.strip() for line in rules.splitlines() if line.strip()}
    for pattern, why in EXPECTED_IGNORES:
        if pattern in patterns or f"/{pattern}" in patterns:
            report.tally("ignore-rules-held")
            continue
        report.add(
            "ignore-rule-missing",
            Severity.MEDIUM,
            ".gitignore",
            f"the ignore file does not hold out {pattern}",
            hint=f"{why}; the pre-push check refuses the path by name as well",
        )


def hook_findings(root: Path, report: Report) -> None:
    """Whether the hooks this repository installs are installed here."""
    for name in ("pre-commit", "pre-push"):
        hook = root / ".git" / "hooks" / name
        if hook.is_file():
            report.tally("hooks-installed")
            continue
        report.add(
            f"hook-{name}-absent",
            Severity.NOTE,
            f".git/hooks/{name}",
            f"the {name} hook is not installed in this clone",
            hint="python3 tools/security/scan_secrets.py --install-hook writes both",
        )


def scan(root: Path) -> Report:
    """Every reading this scanner takes over the tracked tree."""
    report = Report(name="scan_files", surface="tracked")
    try:
        entries = index_entries(root)
    except RuntimeError as error:
        report.add("index-unreadable", Severity.HIGH, ".git/index", str(error))
        return report
    report.tally("tracked-files", len(entries))
    mode_findings(root, entries, report)
    name_findings(entries, report)
    ignore_findings(root, report)
    hook_findings(root, report)

    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scan_files",
        description="Read the tracked tree for modes, names and the rules that hold them out.",
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
        print(f"scan_files: {error}", file=sys.stderr)
        return 2

    report = scan(root)
    level = Severity(args.fail_on)
    failing = report.at(level)
    if not args.quiet:
        for finding in report.at(Severity.NOTE):
            print(finding.render())
    print(report.summary())
    print(f"scan_files: {len(failing)} finding(s) at or above {level.value}")

    if args.json:
        destination = Path(args.json)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(report.to_json() + "\n", encoding="utf-8")

    return 1 if failing else 0


if __name__ == "__main__":
    raise SystemExit(main())
