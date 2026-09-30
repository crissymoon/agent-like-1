"""The runs as one dataset, and the reading that turns it into work on the harness.

A benchmark result is only useful if something can be done with it. The tables
this module writes are for that: every run flattened to one row per side, solver
and task, with the counters the loop recorded left in their own columns rather
than folded into the composite, so a row can be filtered and joined by something
other than this file.

On top of the rows sits the part that names the work. Every failed row is read
against a small table of signals, each of which is a measurable fact about the
row rather than an opinion about it, and each of which names the part of the
harness it points at: the output contract, the tool catalog, the argument
schema, the retry rule, the stop condition, the plan, or the verifier. The
signals are stated as data in :data:`SIGNALS`, the reading they support is a
report in ``learning.json``, and the mapping from signal to part of the harness
is deliberately short: it says where to look, not what to change.

The dataset is what a model-side improvement is measured against later, so the
rows carry the solver label and the side and nothing about this machine.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gembench import compare, runner, scoring  # noqa: E402

SCHEMA_VERSION = "1"

#: The column a row carries to say which side of the run it came from. It is not
#: in :data:`runner.COLUMNS` because a run written on a machine does not know it
#: is the local side.
SIDE_COLUMN = "side"

#: The two columns this module adds after the run's own.
SIGNAL_COLUMN = "signals"
AREA_COLUMN = "areas"

#: The measurements a failed row is read against, and the part of the harness
#: each one points at. The predicate is named here and defined in
#: :data:`SIGNAL_TESTS`, so the table and the arithmetic sit next to each other.
SIGNALS: tuple[dict[str, str], ...] = (
    {
        "name": "ran_out_of_turns",
        "area": "loop_control",
        "note": "the episode spent its whole step budget: the stop condition or the length of a plan is the lever",
    },
    {
        "name": "failed_with_turns_left",
        "area": "planning",
        "note": "the episode failed while steps remained, so the plan was wrong rather than long",
    },
    {
        "name": "unstructured_action",
        "area": "output_contract",
        "note": "an intended action never became a structured call: the format the loop reads is the lever",
    },
    {
        "name": "unknown_tool",
        "area": "tool_catalog",
        "note": "a tool was named that the harness does not have: the catalog or the way it is prompted is the lever",
    },
    {
        "name": "invalid_arguments",
        "area": "argument_schema",
        "note": "a call was refused on its arguments: the schema or the examples beside it are the lever",
    },
    {
        "name": "tool_error_unrecovered",
        "area": "error_handling",
        "note": "a tool error was met and not recovered from: the retry rule is the lever",
    },
    {
        "name": "repeated_failing_turn",
        "area": "error_handling",
        "note": "the same failing turn was repeated: the loop needs to change what it does on a repeat",
    },
    {
        "name": "redundant_work",
        "area": "efficiency",
        "note": "calls were repeated that a shorter path skips: the plan or the memory of what was already done is the lever",
    },
    {
        "name": "partial_checks",
        "area": "verification",
        "note": "some of the task's own checks passed: the verifier names exactly what the end state missed",
    },
    {
        "name": "answer_not_given",
        "area": "stopping_criteria",
        "note": "the honest answer was not given on a task whose goal is to stop: refusal has to be a teachable output",
    },
)


def _number(row: dict, key: str) -> float:
    try:
        return float(row.get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def _flag(row: dict, key: str) -> bool:
    return _number(row, key) > 0.5


def _ran_out_of_turns(row: dict) -> bool:
    return _flag(row, "budget_exhausted") and not _flag(row, "success")


def _failed_with_turns_left(row: dict) -> bool:
    return not _flag(row, "success") and not _flag(row, "budget_exhausted")


def _unstructured_action(row: dict) -> bool:
    return _number(row, "unstructured_tool_calls") > 0


def _unknown_tool(row: dict) -> bool:
    return _number(row, "unknown_tools") > 0


def _invalid_arguments(row: dict) -> bool:
    return _number(row, "invalid_args") > 0


def _tool_error_unrecovered(row: dict) -> bool:
    return _number(row, "tool_errors") > 0 and not _flag(row, "recovered")


def _repeated_failing_turn(row: dict) -> bool:
    return _number(row, "repeated_failed_turns") > 0


def _redundant_work(row: dict) -> bool:
    return _number(row, "redundant_calls") > 0


def _partial_checks(row: dict) -> bool:
    total = _number(row, "checks_total")
    return total > 0 and _number(row, "checks_passed") < total


def _answer_not_given(row: dict) -> bool:
    return row.get("capability") == "stopping_criteria" and not _flag(row, "success")


#: Signal name to the reading of one row. A row that passes is read too, and
#: reads as nothing, which is why every test states the failure it looks for.
SIGNAL_TESTS = {
    "ran_out_of_turns": _ran_out_of_turns,
    "failed_with_turns_left": _failed_with_turns_left,
    "unstructured_action": _unstructured_action,
    "unknown_tool": _unknown_tool,
    "invalid_arguments": _invalid_arguments,
    "tool_error_unrecovered": _tool_error_unrecovered,
    "repeated_failing_turn": _repeated_failing_turn,
    "redundant_work": _redundant_work,
    "partial_checks": _partial_checks,
    "answer_not_given": _answer_not_given,
}


def read_run_rows(directory: Path) -> dict[str, dict]:
    """One run's rows, every column kept as it was written."""
    path = Path(directory) / "tasks.csv"
    with path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"{path} is empty")
        missing = [name for name in compare.REQUIRED_COLUMNS if name not in reader.fieldnames]
        if missing:
            raise ValueError(f"{path} is missing column(s): {', '.join(missing)}")
        rows: dict[str, dict] = {}
        for raw in reader:
            task_id = str(raw.get("task_id", ""))
            if task_id:
                rows[task_id] = dict(raw)
        return rows


def read_sides(sides: dict[str, Path]) -> list[dict]:
    """Every run under every side, with the side and the run's directory attached."""
    runs: list[dict] = []
    for side, root in sides.items():
        for directory in compare.run_directories(Path(root)):
            try:
                rows = read_run_rows(directory)
            except (ValueError, OSError):
                continue
            if not rows:
                continue
            first = next(iter(rows.values()))
            manifest = {}
            manifest_path = directory / "manifest.json"
            if manifest_path.is_file():
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            runs.append(
                {
                    "side": side,
                    "model": str(first.get("model", directory.name)),
                    "run_id": str(first.get("run_id", directory.name)),
                    "suite": str(manifest.get("run", {}).get("suite", "")),
                    "profile": str(first.get("tool_mode", "")),
                    "directory": str(directory),
                    "rows": list(rows.values()),
                }
            )
    return runs


def signals_for(row: dict) -> list[str]:
    return [name for name, test in SIGNAL_TESTS.items() if test(row)]


def area_of(signal: str) -> str:
    for entry in SIGNALS:
        if entry["name"] == signal:
            return entry["area"]
    return ""


def areas_for(row: dict) -> list[str]:
    seen: list[str] = []
    for signal in signals_for(row):
        area = area_of(signal)
        if area and area not in seen:
            seen.append(area)
    return seen


def flatten(runs: list[dict]) -> list[dict]:
    """One row per side, solver and task, in the run's own column order."""
    flat: list[dict] = []
    for run in runs:
        for row in run["rows"]:
            record = {SIDE_COLUMN: run["side"]}
            for column in runner.COLUMNS:
                record[column] = row.get(column, "")
            found = signals_for(row)
            record[SIGNAL_COLUMN] = " ".join(found)
            record[AREA_COLUMN] = " ".join(areas_for(row))
            flat.append(record)
    return flat


def columns() -> list[str]:
    return [SIDE_COLUMN] + list(runner.COLUMNS) + [SIGNAL_COLUMN, AREA_COLUMN]


def records(runs: list[dict]) -> list[dict]:
    """The same rows as objects, with the counters and the checks kept separate.

    A flat CSV is what a spreadsheet and a join want; a record that keeps the
    counters in their own object is what a training script wants, because a
    counter is not a feature until someone says it is.
    """
    out: list[dict] = []
    for run in runs:
        for row in run["rows"]:
            found = signals_for(row)
            out.append(
                {
                    "schema_version": SCHEMA_VERSION,
                    "side": run["side"],
                    "solver": run["model"],
                    "run_id": run["run_id"],
                    "tool_mode": str(row.get("tool_mode", "")),
                    "task": {
                        "id": str(row.get("task_id", "")),
                        "capability": str(row.get("capability", "")),
                        "budget": int(_number(row, "budget")),
                    },
                    "outcome": {
                        "success": _flag(row, "success"),
                        "composite": _number(row, "composite"),
                        "checks_passed": int(_number(row, "checks_passed")),
                        "checks_total": int(_number(row, "checks_total")),
                    },
                    "dimensions": {name: _number(row, name) for name in scoring.DIMENSIONS},
                    "counters": {name: int(_number(row, name)) for name in scoring.COUNTERS},
                    "cost": {
                        "steps_used": int(_number(row, "steps_used")),
                        "budget_exhausted": _flag(row, "budget_exhausted"),
                        "latency_ms": _number(row, "latency_ms"),
                    },
                    "signals": found,
                    "areas": areas_for(row),
                }
            )
    return out


def learning(runs: list[dict]) -> dict:
    """Where the lost composite sits, grouped by the part of the harness that a signal names.

    The figure that orders the work is the composite a solver did not get: a
    dimension averaged over the suite hides the four tasks a solver missed while
    scoring near a hundred on the five it passed, and the four tasks are the
    work.
    """
    by_area: dict[str, dict] = {}
    by_capability: dict[str, dict] = {}
    by_solver: dict[str, dict] = {}

    for run in runs:
        label = f"{run['side']}/{run['model']}"
        solver = by_solver.setdefault(
            label, {"tasks": 0, "passed": 0, "lost": 0.0, "dimensions": {name: 0.0 for name in scoring.DIMENSIONS}}
        )
        for row in run["rows"]:
            solver["tasks"] += 1
            solver["passed"] += 1 if _flag(row, "success") else 0
            solver["lost"] = round(solver["lost"] + (scoring.SCORE_SCALE - _number(row, "composite")), 4)
            for name in scoring.DIMENSIONS:
                solver["dimensions"][name] = round(solver["dimensions"][name] + _number(row, name), 4)

            capability = str(row.get("capability", ""))
            bucket = by_capability.setdefault(
                capability, {"tasks": 0, "passed": 0, "lost": 0.0, "solvers": []}
            )
            bucket["tasks"] += 1
            bucket["passed"] += 1 if _flag(row, "success") else 0
            bucket["lost"] = round(bucket["lost"] + (scoring.SCORE_SCALE - _number(row, "composite")), 4)
            if label not in bucket["solvers"]:
                bucket["solvers"].append(label)

            for signal in signals_for(row):
                area = area_of(signal)
                if not area:
                    continue
                group = by_area.setdefault(
                    area, {"tasks": 0, "lost": 0.0, "signal_counts": {}, "tasks_named": [], "solvers": [], "note": ""}
                )
                group["tasks"] += 1
                group["lost"] = round(group["lost"] + (scoring.SCORE_SCALE - _number(row, "composite")), 4)
                group["signal_counts"][signal] = group["signal_counts"].get(signal, 0) + 1
                task_id = str(row.get("task_id", ""))
                if task_id not in group["tasks_named"]:
                    group["tasks_named"].append(task_id)
                if label not in group["solvers"]:
                    group["solvers"].append(label)
                group["note"] = next((entry["note"] for entry in SIGNALS if entry["name"] == signal), "")

    for label, solver in by_solver.items():
        solver["dimensions"] = {
            name: round(value / max(1, solver["tasks"]), 4) for name, value in solver["dimensions"].items()
        }

    ordered = sorted(by_area.items(), key=lambda item: (-item[1]["lost"], item[0]))
    return {
        "document": "benchmark-learning",
        "schema_version": SCHEMA_VERSION,
        "solvers": by_solver,
        "capabilities": by_capability,
        "areas": {name: values for name, values in ordered},
        "priority": [name for name, _ in ordered],
    }


def render(reading: dict) -> str:
    """The reading as lines, with the work ordered by the composite it accounts for."""
    lines = ["Where the lost composite sits", ""]
    lines.append("  one row per side, solver and task, so a solver measured on two sides is counted twice")
    lines.append("")
    if not reading["areas"]:
        lines.append("  no failed row carried a signal, so nothing is named")
        return "\n".join(lines) + "\n"

    lines.append(f"  {'area':<20} {'tasks':>5} {'lost':>8}  signals")
    for area, values in reading["areas"].items():
        counts = ", ".join(f"{name} {count}" for name, count in sorted(values["signal_counts"].items()))
        lines.append(f"  {area:<20} {values['tasks']:>5} {values['lost']:>8.2f}  {counts}")

    lines += ["", "  the lever beside each area", ""]
    for area, values in reading["areas"].items():
        lines.append(f"    {area}: {values['note']}")
    lines += ["", "  by capability", ""]
    for capability, values in sorted(reading["capabilities"].items()):
        lines.append(
            f"    {capability:<24} passed {values['passed']}/{values['tasks']}  lost {values['lost']:.2f}"
        )
    lines += ["", "  by solver", ""]
    for label, values in sorted(reading["solvers"].items()):
        lines.append(f"    {label:<34} passed {values['passed']}/{values['tasks']}  lost {values['lost']:.2f}")
    return "\n".join(lines) + "\n"


def write(out_dir: Path, runs: list[dict], document: dict | None = None) -> dict[str, str]:
    """Write the dataset, the records, the signal table and the reading."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    flat = flatten(runs)
    csv_path = out_dir / "dataset.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns())
        writer.writeheader()
        for row in flat:
            writer.writerow({name: row.get(name, "") for name in columns()})

    rows = records(runs)
    jsonl_path = out_dir / "dataset.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as handle:
        for entry in rows:
            handle.write(json.dumps(entry, sort_keys=False) + "\n")

    signals_path = out_dir / "signals.json"
    signals_path.write_text(
        json.dumps(
            {
                "document": "benchmark-signals",
                "schema_version": SCHEMA_VERSION,
                "signals": [dict(entry) for entry in SIGNALS],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    reading = learning(runs)
    learning_path = out_dir / "learning.json"
    learning_path.write_text(json.dumps(reading, indent=2) + "\n", encoding="utf-8")

    note_path = out_dir / "learning.txt"
    note_path.write_text(render(reading), encoding="utf-8")

    descriptor = {
        "document": "benchmark-dataset",
        "schema_version": SCHEMA_VERSION,
        "rows": len(flat),
        "records": len(rows),
        "columns": columns(),
        "sides": sorted({run["side"] for run in runs}),
        "solvers": sorted({f"{run['side']}/{run['model']}" for run in runs}),
        "areas": reading["priority"],
        "files": {
            "csv": csv_path.name,
            "jsonl": jsonl_path.name,
            "signals": signals_path.name,
            "learning": learning_path.name,
            "reading": note_path.name,
        },
    }
    if document is not None:
        descriptor["comparison"] = {
            "columns": list(document.get("columns", {})),
            "tasks": len(document.get("tasks", [])),
            "findings": list(document.get("findings", [])),
        }
    descriptor_path = out_dir / "dataset.json"
    descriptor_path.write_text(json.dumps(descriptor, indent=2) + "\n", encoding="utf-8")

    return {
        "csv": str(csv_path),
        "jsonl": str(jsonl_path),
        "signals": str(signals_path),
        "learning": str(learning_path),
        "reading": str(note_path),
        "descriptor": str(descriptor_path),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gembench.dataset",
        description="Write the runs as one dataset and read the failures as work.",
    )
    parser.add_argument("--side", action="append", default=[], metavar="NAME=PATH")
    parser.add_argument("--out", default="", help="directory to write the dataset into")
    parser.add_argument("--json", action="store_true", help="print the reading instead of the table")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    sides: dict[str, Path] = {}
    for item in args.side:
        if "=" not in item:
            print(f"--side expects NAME=PATH, got {item!r}")
            return 2
        name, _, path = item.partition("=")
        sides[name.strip()] = Path(path.strip()).expanduser()

    if not sides:
        print("at least one --side NAME=PATH is needed")
        return 2

    runs = read_sides(sides)
    if not runs:
        print("no run was read from " + ", ".join(f"{name}={root}" for name, root in sides.items()))
        return 3

    reading = learning(runs)
    if args.json:
        print(json.dumps(reading, indent=2))
    else:
        print(render(reading), end="")

    if args.out:
        files = write(Path(args.out), runs)
        print()
        for name, path in files.items():
            print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
