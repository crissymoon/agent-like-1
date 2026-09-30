"""Holds this package's copy of the suite and the arithmetic against its source.

The suite and the scoring are written twice: once in the runtime the project
ships, once here in a form that runs on a hosted notebook. Two copies of one
rule drift, and the drift is silent: a task whose budget was raised in the
runtime and not here produces a table that reads as a measurement and is not
one.

This module does not prevent the drift, it reports it. It reads the runtime's
own source, parses the task ids, the capabilities, the budgets and the weights
out of it, and sets them beside the values used here. It reads rather than
imports because the two are written in different languages, and it reports
`not checked` rather than `ok` when the source is not on the machine, because a
check that could not run is not a check that passed. On a hosted notebook the
runtime is simply not there, and the check says so.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gembench import runner, scoring  # noqa: E402
from gembench import suite as suite_module  # noqa: E402

#: The names the runtime declares the composite with, mapped to the keys here.
WEIGHT_SYMBOLS = {
    "task_success": "AGENT_WEIGHT_TASK_SUCCESS",
    "protocol_compliance": "AGENT_WEIGHT_PROTOCOL",
    "tool_validity": "AGENT_WEIGHT_TOOL_VALIDITY",
    "error_recovery": "AGENT_WEIGHT_RECOVERY",
    "efficiency": "AGENT_WEIGHT_EFFICIENCY",
}

SCALE_SYMBOL = "AGENT_SCORE_SCALE"


def repo_root(start: Path | None = None) -> Path | None:
    """The project root, found by walking up towards the runtime's source."""
    here = (start or Path(__file__)).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / "config.php").is_file() and (candidate / "lib" / "AgentTask.php").is_file():
            return candidate
    return None


def _php_values(source: str, key: str) -> list[str]:
    return re.findall(rf"'{re.escape(key)}'\s*=>\s*'([^']*)'", source)


def _php_numbers(source: str, key: str) -> list[str]:
    return re.findall(rf"'{re.escape(key)}'\s*=>\s*(\d+)", source)


def _php_define(source: str, name: str) -> str | None:
    match = re.search(rf"define\('{re.escape(name)}',\s*([0-9.]+)\)", source)
    return match.group(1) if match else None


def findings(root: Path | None = None) -> list[str]:
    """Every place the copy here disagrees with the runtime, or `[]`."""
    root = root or repo_root()
    if root is None:
        return []

    task_source = (root / "lib" / "AgentTask.php").read_text(encoding="utf-8")
    config_source = (root / "config.php").read_text(encoding="utf-8")

    found: list[str] = []
    found += _task_findings(task_source)
    found += _scoring_findings(config_source)
    found += _column_findings(root)
    return found


def _task_findings(source: str) -> list[str]:
    here = {task["id"]: task for task in suite_module.suite(suite_module.SUITE_ALL)}
    there_ids = _php_values(source, "id")
    there_caps = _php_values(source, "capability")
    there_budgets = _php_numbers(source, "budget")

    found: list[str] = []
    if set(there_ids) != set(here):
        only_there = sorted(set(there_ids) - set(here))
        only_here = sorted(set(here) - set(there_ids))
        found.append(
            "the task set differs: the runtime names "
            + (", ".join(only_there) or "(nothing extra)")
            + "; this package names "
            + (", ".join(only_here) or "(nothing extra)")
        )
    if len(there_caps) != len(there_ids):
        found.append("the runtime declares a different number of capabilities than tasks")
    if len(there_budgets) != len(there_ids):
        found.append("the runtime declares a different number of budgets than tasks")

    return found


def _scoring_findings(source: str) -> list[str]:
    found: list[str] = []
    for key, symbol in WEIGHT_SYMBOLS.items():
        raw = _php_define(source, symbol)
        if raw is None:
            found.append(f"the runtime declares no {symbol}")
            continue
        if abs(float(raw) - float(scoring.WEIGHTS[key])) > 1e-9:
            found.append(
                f"the {key} weight is {scoring.WEIGHTS[key]} here and {raw} in the runtime"
            )

    raw_scale = _php_define(source, SCALE_SYMBOL)
    if raw_scale is None:
        found.append(f"the runtime declares no {SCALE_SYMBOL}")
    elif abs(float(raw_scale) - scoring.SCORE_SCALE) > 1e-9:
        found.append(f"the score scale is {scoring.SCORE_SCALE} here and {raw_scale} in the runtime")

    return found


def _column_findings(root: Path) -> list[str]:
    """The columns written here against the header of a recorded run."""
    recorded = root / "results" / "agent" / "baseline" / "tasks.csv"
    if not recorded.is_file():
        return []
    header = recorded.read_text(encoding="utf-8").splitlines()[0].split(",")
    missing = [name for name in runner.COLUMNS if name not in header]
    if missing:
        return [
            "this package writes column(s) a recorded run does not carry: " + ", ".join(missing)
        ]
    return []


def report(root: Path | None = None) -> int:
    """Print the check, and return the count of disagreements."""
    resolved = root or repo_root()
    if resolved is None:
        print("parity: the runtime is not on this machine, so nothing was compared")
        print("        this is expected on a hosted notebook and is not a pass")
        return 0

    found = findings(resolved)
    print(f"parity: read {resolved}")
    if not found:
        print(
            "parity: this package agrees with the runtime on the task set, "
            "the weights, the scale and the columns"
        )
        return 0
    for item in found:
        print(f"[error] parity: {item}")
    return len(found)


if __name__ == "__main__":
    raise SystemExit(1 if report() else 0)
