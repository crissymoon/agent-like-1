"""Run the merged Q2 planner-coder service over the portable suite.

This is the caller `hybrid` was written for. It registers the live solver under
a profile name, refuses to start when the merged service is not answering, runs
the suite through the same `runner` the fixtures use, writes the run beside the
recorded ones, and prints the comparison against the side it was told to read.

Nothing here is specific to one machine. The service is a URL, the output is a
directory and the suite is a name, so the same command that measures the merged
model on this laptop measures it, unchanged, wherever the two halves are served.

    python3 tools/kaggle/gembench/run_hybrid.py \\
        --url http://127.0.0.1:8730 \\
        --out results/benchmark/kaggle \\
        --compare results/benchmark/kaggle \\
        --check
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if __package__ in (None, ""):
    sys.path.insert(0, str(HERE.parent))

from gembench import compare, hybrid, runner  # noqa: E402
from gembench import suite as suite_module  # noqa: E402

DEFAULT_PROFILE = "q2-hybrid"
DEFAULT_URL = "http://127.0.0.1:8730"
DEFAULT_MODEL = "q2-planner-coder"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gembench.run_hybrid",
        description="Measure the merged Q2 planner-coder service against the task suite.",
    )
    parser.add_argument("--url", default=DEFAULT_URL, help="base URL of the merged service")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="the label the rows carry")
    parser.add_argument("--profile", default=DEFAULT_PROFILE, help="profile name to register the solver under")
    parser.add_argument("--suite", default=suite_module.SUITE_ALL, help="task suite to run")
    parser.add_argument("--run-id", default="", help="the run's directory name (default: the model label)")
    parser.add_argument("--out", default="", help="directory to write the run into; omit to print only")
    parser.add_argument("--timeout", type=float, default=300.0, help="seconds for one merged turn")
    parser.add_argument("--compare", default="", help="a results directory to read beside this run")
    parser.add_argument("--skip-probe", action="store_true", help="run without asking /health first")
    parser.add_argument("--check", action="store_true", help="exit non-zero on an unreadable run")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    client = hybrid.register(
        args.profile, url=args.url, model=args.model, timeout=args.timeout
    )

    if not args.skip_probe:
        try:
            client.require_healthy()
        except hybrid.HybridUnreachable as exc:
            print(f"[error] {exc}", file=sys.stderr)
            return 3

    model = args.model or f"{args.profile}-solver"
    run_id = args.run_id or model
    result = runner.run(args.profile, args.suite, model, run_id)

    if args.out:
        files = runner.write_run(
            Path(args.out) / run_id, result, extra={"profile": args.profile, "service": args.url}
        )
        print(f"wrote {files['tasks']}")
        print(f"wrote {files['manifest']}")

    print(runner.render(result), end="")

    if args.compare:
        sides = {"kaggle": Path(args.compare)}
        if args.out:
            sides["new"] = Path(args.out)
        table, problems = compare.collect(sides)
        document = compare.build(table)
        print(compare.render(document), end="")
        for problem in problems:
            print(f"[warn] {problem}", file=sys.stderr)

    if args.check:
        findings = runner.check(result)
        for finding in findings:
            print(f"[error] {finding}")
        if findings:
            return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
