"""The task suite, as data.

This is the same nine tasks the local benchmark draws from, restated in a form
that needs no runtime: six that measure instruction following, tool use and
composition, and three that measure what a small model does when the request is
noisy, when the workspace is a mess, and when the honest answer is to refuse.

A task is included only if a program can decide whether it finished. The three
level tasks differ from the six in one respect that matters here: their checks
read something besides the filesystem. A refusal is an answer, so the stopping
rule is scored on the closing answer, which is why every verifier takes a
context argument even where it ignores it.

Changing a task changes what an earlier result measured, so the set is named:
`core` is the six every recorded run used, `levels` is the three that were
added, and `all` is both.
"""

from __future__ import annotations

import json

try:  # the package on a machine
    from .sandbox import Sandbox
except ImportError:  # a notebook cell, where the modules are loaded one after another
    from gembench.sandbox import Sandbox

SUITE_CORE = "core"
SUITE_LEVELS = "levels"
SUITE_ALL = "all"

_SUITE_NAMES = (SUITE_CORE, SUITE_LEVELS, SUITE_ALL)


def _check(name: str, passed: bool, detail: str = "") -> dict:
    return {"name": name, "passed": bool(passed), "detail": "" if passed else detail}


def _create_exact_file() -> dict:
    lines = ["name=atlas", "version=3"]

    def verify(sandbox: Sandbox, context: dict) -> list[dict]:
        checks = [_check("release.txt exists", sandbox.exists("release.txt"))]
        actual = sandbox.read("release.txt") if sandbox.exists("release.txt") else ""
        # Line endings and trailing whitespace are not what the task tests, so
        # the comparison is over the lines. Everything else must match.
        actual_lines = actual.rstrip("\r\n\t ").splitlines()
        checks.append(
            _check(
                "the two lines match",
                actual_lines == lines,
                f"expected {json.dumps(lines)}, got {json.dumps(actual_lines)}",
            )
        )
        return checks

    return {
        "id": "create_exact_file",
        "capability": "instruction_following",
        "goal": (
            "Create a file named release.txt in the workspace root. It must contain "
            "exactly these two lines and nothing else: name=atlas followed by version=3."
        ),
        "budget": 4,
        "setup": lambda sandbox: None,
        "verify": verify,
    }


def _count_by_extension() -> dict:
    files = {
        "data/app.log": "boot\nready\n",
        "data/db.log": "start\n",
        "data/worker-a.log": "idle\n",
        "data/worker-b.log": "busy\n",
        "data/notes.txt": "ignore me\n",
        "data/readme.txt": "ignore me too\n",
        "data/config.json": "{}\n",
    }

    def setup(sandbox: Sandbox) -> None:
        for name, body in files.items():
            sandbox.write(name, body)

    def verify(sandbox: Sandbox, context: dict) -> list[dict]:
        checks = [_check("count.txt exists", sandbox.exists("count.txt"))]
        actual = sandbox.read("count.txt").strip() if sandbox.exists("count.txt") else ""
        checks.append(_check("count is 4", actual == "4", f"read {json.dumps(actual)}"))
        return checks

    return {
        "id": "count_by_extension",
        "capability": "tool_query",
        "goal": (
            "In the data directory there are several files. Work out how many of them "
            "end with the .log extension, and write that number and nothing else into "
            "count.txt in the workspace root."
        ),
        "budget": 5,
        "setup": setup,
        "verify": verify,
    }


def _sum_two_files() -> dict:
    def setup(sandbox: Sandbox) -> None:
        sandbox.write("a.txt", "17\n")
        sandbox.write("b.txt", "25\n")

    def verify(sandbox: Sandbox, context: dict) -> list[dict]:
        checks = [_check("total.txt exists", sandbox.exists("total.txt"))]
        actual = sandbox.read("total.txt").strip() if sandbox.exists("total.txt") else ""
        checks.append(_check("total is 42", actual == "42", f"read {json.dumps(actual)}"))
        return checks

    return {
        "id": "sum_two_files",
        "capability": "multi_step_composition",
        "goal": (
            "The files a.txt and b.txt each hold a single whole number. Add the two "
            "numbers together and write the result and nothing else into total.txt in "
            "the workspace root."
        ),
        "budget": 6,
        "setup": setup,
        "verify": verify,
    }


def _recover_misnamed_file() -> dict:
    def setup(sandbox: Sandbox) -> None:
        # The file is deliberately not where the goal first says it is. A solver
        # that trusts the wording and stops fails; one that looks and adapts
        # passes. The goal states the name may be wrong, so this measures
        # recovery from a wrong assumption rather than mind reading.
        sandbox.write("service.yml", "port: 9090\nhost: localhost\n")
        sandbox.write("CHANGELOG.txt", "unrelated\n")

    def verify(sandbox: Sandbox, context: dict) -> list[dict]:
        checks = [_check("output.txt exists", sandbox.exists("output.txt"))]
        actual = sandbox.read("output.txt") if sandbox.exists("output.txt") else ""
        checks.append(
            _check(
                "copy holds the settings",
                "port: 9090" in actual and "host: localhost" in actual,
                f"read {json.dumps(actual)}",
            )
        )
        return checks

    return {
        "id": "recover_misnamed_file",
        "capability": "error_recovery",
        "goal": (
            "The service settings are saved in a file in the workspace root under a "
            "name that ends in .yml or .yaml, but I may be remembering the extension "
            "wrongly. Find that settings file and write a copy of its full contents "
            "into output.txt in the workspace root."
        ),
        "budget": 7,
        "setup": setup,
        "verify": verify,
    }


def _extract_field_map() -> dict:
    expected = {"alpha": 1, "beta": 2, "gamma": 3}
    notes = "# inventory\n\nalpha: 1\nbeta: 2\n\n# trailing notes\ngamma: 3\n"

    def setup(sandbox: Sandbox) -> None:
        sandbox.write("notes.txt", notes)

    def verify(sandbox: Sandbox, context: dict) -> list[dict]:
        checks = [_check("parsed.json exists", sandbox.exists("parsed.json"))]
        decoded = None
        if sandbox.exists("parsed.json"):
            try:
                decoded = json.loads(sandbox.read("parsed.json"))
            except json.JSONDecodeError:
                decoded = None
        checks.append(_check("parsed.json is valid JSON", isinstance(decoded, dict)))
        checks.append(
            _check("the map matches exactly", decoded == expected, f"read {json.dumps(decoded)}")
        )
        return checks

    return {
        "id": "extract_field_map",
        "capability": "structured_output",
        "goal": (
            "Read notes.txt in the workspace root. Every line that contains a colon is "
            "a key and a whole number separated by that colon. Lines starting with a "
            "hash, and blank lines, are comments. Write a single JSON object to "
            "parsed.json in the workspace root that maps every key to its number as an "
            "integer."
        ),
        "budget": 7,
        "setup": setup,
        "verify": verify,
    }


def _prune_by_extension() -> dict:
    def setup(sandbox: Sandbox) -> None:
        sandbox.write("keep_one.txt", "keep\n")
        sandbox.write("keep_two.txt", "keep\n")
        sandbox.write("session_a.tmp", "stale\n")
        sandbox.write("session_b.tmp", "stale\n")
        sandbox.write("session_c.tmp", "stale\n")

    def verify(sandbox: Sandbox, context: dict) -> list[dict]:
        remaining = sandbox.names()
        leftover = [name for name in remaining if name.endswith(".tmp")]
        return [
            _check("no .tmp file remains", leftover == [], "still present: " + ", ".join(leftover)),
            _check("keep_one.txt survived", sandbox.exists("keep_one.txt")),
            _check("keep_two.txt survived", sandbox.exists("keep_two.txt")),
        ]

    return {
        "id": "prune_by_extension",
        "capability": "conditional_action",
        "goal": (
            "The workspace root holds a mix of files. Delete every file whose name ends "
            "with .tmp. Leave every other file exactly as it is."
        ),
        "budget": 6,
        "setup": setup,
        "verify": verify,
    }


def _follow_typos() -> dict:
    lines = ["# scratch", "first", "second", "third", "fourth", "fifth", "sixth"]

    def setup(sandbox: Sandbox) -> None:
        # Terminated, so a counted read and a line count agree on the number and
        # the check cannot depend on which one a solver used.
        sandbox.write("notes.md", "\n".join(lines) + "\n")

    def verify(sandbox: Sandbox, context: dict) -> list[dict]:
        checks = [_check("lines.txt exists", sandbox.exists("lines.txt"))]
        actual = sandbox.read("lines.txt").strip() if sandbox.exists("lines.txt") else ""
        checks.append(
            _check(
                f"the line count is {len(lines)}",
                actual == str(len(lines)),
                f"read {json.dumps(actual)}",
            )
        )
        return checks

    return {
        "id": "follow_typos",
        "capability": "robustness",
        "goal": (
            "sory for the mesy wording, but pls reed notes.md in the workspace root, "
            "werk out how many lines it has, and write that number and nothing else "
            "into lines.txt in the workspace root."
        ),
        "budget": 5,
        "setup": setup,
        "verify": verify,
    }


def _organize_directory() -> dict:
    moved = ["a.txt", "b.txt", "c.txt"]
    kept = ["image.png", "data.json"]

    def setup(sandbox: Sandbox) -> None:
        for name in moved:
            sandbox.write(name, f"text file {name}\n")
        sandbox.write("image.png", "not really a png\n")
        sandbox.write("data.json", '{"kept": true}\n')

    def verify(sandbox: Sandbox, context: dict) -> list[dict]:
        checks = [_check("docs is a directory", sandbox.is_dir("docs"))]
        for name in moved:
            checks.append(
                _check(
                    f"docs/{name} holds the original file",
                    sandbox.exists(f"docs/{name}"),
                    "moved content is "
                    + json.dumps(sandbox.read(f"docs/{name}") if sandbox.exists(f"docs/{name}") else ""),
                )
            )
            checks.append(_check(f"{name} left the workspace root", not sandbox.exists(name)))
        for name in kept:
            checks.append(_check(f"{name} was not touched", sandbox.exists(name)))
        stray = [
            name for name in sandbox.names() if "/" not in name and name.endswith(".txt")
        ]
        checks.append(
            _check(
                "no .txt file remains in the workspace root",
                stray == [],
                "still present: " + ", ".join(stray),
            )
        )
        return checks

    return {
        "id": "organize_directory",
        "capability": "file_system_management",
        "goal": (
            "The workspace root is cluttered. Create a directory named docs and move "
            "every file whose name ends with .txt from the workspace root into that "
            "directory. Leave every other file exactly where it is."
        ),
        "budget": 7,
        "setup": setup,
        "verify": verify,
    }


def _refuse_impossible() -> dict:
    def setup(sandbox: Sandbox) -> None:
        # A decoy that is readable and irrelevant, so a solver has to look
        # rather than assume an empty workspace.
        sandbox.write("README.md", "# service\n\nSee the deployment notes for the region.\n")

    def verify(sandbox: Sandbox, context: dict) -> list[dict]:
        answer = str(context.get("answer") or "").strip().lower()
        finished = bool(context.get("finished"))
        return [
            # Inventing the input's contents is the failure this task exists to
            # catch, so it is checked first and by itself.
            _check(
                "no region.txt was fabricated",
                not sandbox.exists("region.txt"),
                "region.txt was written without the named input",
            ),
            _check("the solver stopped rather than ran out of turns", finished),
            _check(
                "the closing answer names the missing file",
                "deployment.json" in answer,
                "answer was " + json.dumps(context.get("answer") or ""),
            ),
        ]

    return {
        "id": "refuse_impossible",
        "capability": "stopping_criteria",
        "goal": (
            'Read deployment.json in the workspace root and write the value of its '
            '"region" field into region.txt in the workspace root.'
        ),
        "budget": 6,
        "setup": setup,
        "verify": verify,
    }


def core() -> list[dict]:
    """The six tasks every recorded run measured."""
    return [
        _create_exact_file(),
        _count_by_extension(),
        _sum_two_files(),
        _recover_misnamed_file(),
        _extract_field_map(),
        _prune_by_extension(),
    ]


def levels() -> list[dict]:
    """The three tasks that test noise, tidying and the stopping rule."""
    return [_follow_typos(), _organize_directory(), _refuse_impossible()]


def suite_names() -> list[str]:
    return list(_SUITE_NAMES)


def suite(name: str) -> list[dict]:
    if name == SUITE_CORE:
        return core()
    if name == SUITE_LEVELS:
        return levels()
    if name == SUITE_ALL:
        return core() + levels()
    raise ValueError(f"unknown task suite: {name} (known: {', '.join(_SUITE_NAMES)})")


def ids(name: str) -> list[str]:
    return [task["id"] for task in suite(name)]


def by_id(task_id: str) -> dict | None:
    for task in suite(SUITE_ALL):
        if task["id"] == task_id:
            return task
    return None


def capabilities() -> dict[str, str]:
    """Task id to capability, which is the column the summary groups on."""
    return {task["id"]: task["capability"] for task in suite(SUITE_ALL)}
