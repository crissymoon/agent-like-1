"""Sets two runtimes' results beside each other.

The question a hosted run raises is whether it measured the same thing as the
run on the machine. That question is answerable, and only answerable, by reading
both runs as the same table: the same suite, the same columns, the same
arithmetic. A side is a directory holding runs; the printed table has one column
per side and model, one row per task.

The comparison does not decide which side is better, because there is nothing to
decide: the same profile on two runtimes is expected to agree. Where a row
differs, the difference is the finding, and each one is reported with the task
and the two composites it read. Where the sides measured different task sets the
table still lands, because a comparison across two suites is a finding rather
than an error.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

#: The columns a run's CSV must carry to be read at all. Read by name, so a
#: column added to a run does not shift every figure in the table.
REQUIRED_COLUMNS = (
    "model",
    "task_id",
    "capability",
    "success",
    "composite",
    "steps_used",
    "budget",
)


def run_directories(root: Path) -> list[Path]:
    """Every directory at or one level below *root* holding a run's CSV."""
    root = Path(root)
    if not root.is_dir():
        return []
    found: list[Path] = []
    if (root / "tasks.csv").is_file():
        found.append(root)
    for entry in sorted(root.iterdir()):
        if entry.is_dir() and (entry / "tasks.csv").is_file():
            found.append(entry)
    return found


def read_run(directory: Path) -> dict[str, dict]:
    """One run's rows, keyed by task id, read by column name."""
    path = directory / "tasks.csv"
    with path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"{path} is empty")
        missing = [name for name in REQUIRED_COLUMNS if name not in reader.fieldnames]
        if missing:
            raise ValueError(f"{path} is missing column(s): {', '.join(missing)}")

        rows: dict[str, dict] = {}
        for raw in reader:
            task_id = str(raw["task_id"])
            if not task_id:
                continue
            if task_id in rows:
                raise ValueError(f"{path} carries {task_id} twice")
            rows[task_id] = {
                "model": str(raw["model"]),
                "task_id": task_id,
                "capability": str(raw["capability"]),
                "success": str(raw["success"]) == "1",
                "composite": float(raw["composite"]),
                "steps_used": int(raw["steps_used"]),
                "budget": int(raw["budget"]),
            }
        return rows


def collect(sides: dict[str, Path]) -> tuple[dict, list[str]]:
    """Read every side, returning the table and any run that could not be read."""
    table: dict[str, dict[str, dict]] = {}
    problems: list[str] = []

    for side, root in sides.items():
        columns: dict[str, dict] = {}
        for directory in run_directories(root):
            try:
                rows = read_run(directory)
            except (ValueError, OSError) as error:
                problems.append(f"{side}/{directory.name}: {error}")
                continue
            if not rows:
                problems.append(f"{side}/{directory.name}: no rows")
                continue
            model = next(iter(rows.values()))["model"]
            if model in columns:
                problems.append(f"{side}/{directory.name}: {model} already read from this side")
                continue
            columns[model] = rows
        table[side] = columns

    return table, problems


def build(table: dict[str, dict[str, dict]]) -> dict:
    """The document the comparison is written from."""
    tasks: dict[str, str] = {}
    for columns in table.values():
        for rows in columns.values():
            for task_id, row in rows.items():
                tasks.setdefault(task_id, row["capability"])

    ordered = sorted(tasks.items(), key=lambda item: (item[1], item[0]))
    keys = [(side, model) for side in table for model in table[side]]

    per_column = {}
    for side, model in keys:
        rows = table[side][model]
        values = [row["composite"] for row in rows.values()]
        per_column[f"{side}/{model}"] = {
            "side": side,
            "model": model,
            "tasks": len(rows),
            "passed": sum(1 for row in rows.values() if row["success"]),
            "composite": round(sum(values) / len(values), 4) if values else 0.0,
        }

    suite_tasks = {task_id for task_id, _ in ordered}
    findings: list[str] = []
    for key, record in per_column.items():
        if record["tasks"] != len(suite_tasks):
            findings.append(
                f"{key} measured {record['tasks']} task(s) where the table holds {len(suite_tasks)}"
            )

    # Where two columns share a model label, their rows are expected to agree.
    by_model: dict[str, list[str]] = {}
    for key, record in per_column.items():
        by_model.setdefault(record["model"], []).append(key)
    disagreements: list[dict] = []
    for model, labels in sorted(by_model.items()):
        if len(labels) < 2:
            continue
        for task_id in sorted(suite_tasks):
            cells = [
                (label, table[per_column[label]["side"]][model][task_id])
                for label in labels
                if task_id in table[per_column[label]["side"]][model]
            ]
            if len(cells) < 2:
                continue
            feet = {round(cell[1]["composite"], 2) for cell in cells}
            if len(feet) > 1:
                disagreements.append(
                    {
                        "model": model,
                        "task_id": task_id,
                        "readings": {label: cell[1]["composite"] for label, cell in cells},
                    }
                )

    return {
        "document": "benchmark-sides",
        "generated_at": __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds"),
        "sides": list(table),
        "columns": per_column,
        "tasks": [{"id": task_id, "capability": capability} for task_id, capability in ordered],
        "matrix": {
            f"{side}/{model}": {
                task_id: {
                    "composite": row["composite"],
                    "success": row["success"],
                    "steps_used": row["steps_used"],
                    "budget": row["budget"],
                }
                for task_id, row in table[side][model].items()
            }
            for side, model in keys
        },
        "findings": findings,
        "disagreements": disagreements,
    }


def render(document: dict) -> str:
    labels = list(document["columns"])
    width = max([len(label) for label in labels] + [5])

    lines = [
        "Benchmark across " + str(len(document["sides"])) + " side(s): "
        + ", ".join(document["sides"]),
        "",
        f"  {'column':<{width}} {'pass':>7} {'composite':>10}",
    ]
    for label in labels:
        record = document["columns"][label]
        lines.append(
            f"  {label:<{width}} {record['passed']:>3}/{record['tasks']:<3} {record['composite']:>10.2f}"
        )

    lines += ["", "  per task, the composite and whether the end state was reached", ""]
    header = f"  {'task':<26} {'capability':<24}" + "".join(f" {label:>{width}}" for label in labels)
    lines.append(header)
    lines.append("  " + "-" * (len(header) - 2))
    for task in document["tasks"]:
        row = f"  {task['id']:<26.26} {task['capability']:<24.24}"
        for label in labels:
            cell = document["matrix"][label].get(task["id"])
            if cell is None:
                row += " " + "--".rjust(width)
            else:
                row += " " + f"{cell['composite']:.1f} {'P' if cell['success'] else 'F'}".rjust(width)
        lines.append(row)

    if document["findings"]:
        lines += ["", "  findings"]
        lines += [f"    - {item}" for item in document["findings"]]

    if document["disagreements"]:
        lines += ["", "  rows where two sides read differently"]
        for item in document["disagreements"]:
            readings = ", ".join(f"{label} {value:.2f}" for label, value in item["readings"].items())
            lines.append(f"    - {item['model']} on {item['task_id']}: {readings}")
    else:
        lines += ["", "  the two sides agree on every shared row"]

    return "\n".join(lines) + "\n"


def write(out_dir: Path, document: dict) -> dict[str, str]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / "sides.json"
    json_path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")

    text_path = out_dir / "sides.txt"
    text_path.write_text(render(document), encoding="utf-8")

    csv_path = out_dir / "sides.csv"
    labels = list(document["columns"])
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["column", "task_id", "capability", "composite", "success", "steps_used"])
        for task in document["tasks"]:
            for label in labels:
                cell = document["matrix"][label].get(task["id"])
                if cell is None:
                    continue
                writer.writerow(
                    [label, task["id"], task["capability"], cell["composite"],
                     1 if cell["success"] else 0, cell["steps_used"]]
                )

    return {"json": str(json_path), "txt": str(text_path), "csv": str(csv_path)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gembench.compare",
        description="Set two runtimes' benchmark results beside each other.",
    )
    parser.add_argument(
        "--side",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help="a side and the directory holding its runs; repeat for each side",
    )
    parser.add_argument("--out", default="", help="where to write the comparison")
    parser.add_argument("--json", action="store_true", help="print the document instead of the table")
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

    if len(sides) < 2:
        print("at least two sides are needed; pass --side NAME=PATH twice")
        return 2

    table, problems = collect(sides)
    if not any(table.values()):
        for name, root in sides.items():
            print(f"{name}: nothing read from {root}")
        return 3

    document = build(table)
    document["findings"].extend(problems)

    if args.out:
        files = write(Path(args.out), document)
        print(f"wrote {files['txt']}")

    if args.json:
        print(json.dumps(document, indent=2))
    else:
        print(render(document), end="")

    return 1 if document["findings"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
