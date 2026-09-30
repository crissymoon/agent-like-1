"""The last step of a run: the pictures, the dataset, and one file that carries both.

A benchmark that ends with a table of numbers on a screen has produced a
screenshot. This module is the step that turns a run into three things a next
person can use: the runs as pictures, so the shape of the failure is read at
once; the runs as one dataset with the failures read as work, so an improvement
can be measured against it; and one zip holding all of it with a manifest and a
link, so it leaves the machine it was measured on.

It is deliberately thin. Every figure, every row and every byte is produced by
:mod:`gembench.charts`, :mod:`gembench.dataset` and :mod:`gembench.bundle`; this
file only decides the order and the directory. The notebook's closing cells call
it, and the same call runs on a machine, so the two sides produce the same
artifacts from the same files.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gembench import bundle, charts, compare, dataset  # noqa: E402

BUNDLE_NAME = bundle.DEFAULT_NAME


def build(sides: dict[str, Path], out_dir: Path, bundle_name: str = BUNDLE_NAME) -> dict:
    """Read the sides, write the comparison, the charts, the dataset and the zip."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    runs = dataset.read_sides(sides)
    table, problems = compare.collect(sides)
    document = compare.build(table)
    document["findings"].extend(problems)

    comparison = compare.write(out_dir, document)

    # One directory for the pictures, and it is the same one the notebook's
    # picture step writes to, so a figure is drawn once and not twice under two
    # names.
    figures_dir = out_dir / "figures"
    figures = charts.write(figures_dir, runs)
    if document["columns"]:
        figures["matrix"] = charts.matrix(figures_dir, document)

    dataset_files = dataset.write(out_dir / "dataset", runs, document)
    reading = dataset.learning(runs)

    built = bundle.build(out_dir, out_dir / bundle_name)

    return {
        "runs": runs,
        "document": document,
        "reading": reading,
        "comparison": comparison,
        "charts": figures,
        "dataset": dataset_files,
        "bundle": built,
    }


def render(record: dict) -> str:
    """The step as lines: what was read, what was written, and where the lost score sits."""
    lines = [f"read {len(record['runs'])} run(s)"]
    for run in record["runs"]:
        passed = sum(1 for row in run["rows"] if str(row.get("success", "")) in {"1", "True", "true"})
        lines.append(f"  {run['side']}/{run['model']:<20} tasks {len(run['rows'])}  passed {passed}")
    lines += ["", "wrote"]
    for name, path in sorted({**record["comparison"], **record["charts"], **record["dataset"]}.items()):
        lines.append(f"  {name:<12} {path}")

    areas = record["reading"]["areas"]
    if areas:
        lines += ["", "the work, ordered by the composite it accounts for", ""]
        for area, values in list(areas.items())[:5]:
            lines.append(f"  {area:<20} lost {values['lost']:>8.2f} over {values['tasks']} row(s)")
            lines.append(f"    {values['note']}")
    else:
        lines += ["", "no failed row carried a signal, so no area is named"]

    lines += ["", bundle.render(record["bundle"]).rstrip("\n")]
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gembench.report",
        description="Write the pictures, the dataset and the bundle of a benchmark run.",
    )
    parser.add_argument("--side", action="append", default=[], metavar="NAME=PATH")
    parser.add_argument("--out", default="", help="directory the run's files are under, and where the bundle is written")
    parser.add_argument("--name", default=BUNDLE_NAME, help="name of the zip inside that directory")
    parser.add_argument("--json", action="store_true", help="print the reading and the manifest as JSON")
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
    if not args.out:
        print("--out is needed: the run's files and the bundle share one directory")
        return 2

    record = build(sides, Path(args.out), args.name)
    if not record["runs"]:
        print("no run was read from " + ", ".join(f"{name}={root}" for name, root in sides.items()))
        return 3

    if args.json:
        print(json.dumps({"reading": record["reading"], "bundle": record["bundle"]}, indent=2))
    else:
        print(render(record), end="")
    return 1 if record["document"]["findings"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
