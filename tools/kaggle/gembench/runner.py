"""Runs a profile over a suite and writes the run down.

One run produces three things, and the shape of all three is chosen so that the
result of this package can be read by the tooling that already reads a local
run: a flat CSV with the same columns, a manifest that names the run, and a
summary in the printed document. Nothing here is Kaggle specific and nothing
here is machine specific, which is the point: the same call produces the local
side and the hosted side, so the two can be set beside each other.

A run is written under its own directory, named by the run id, so a base
directory holding several runs can be read as a table by either this package or
by the runtime that reads the same CSV.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gembench import reference, scoring  # noqa: E402
from gembench import suite as suite_module  # noqa: E402
from gembench.sandbox import Sandbox  # noqa: E402

#: The columns of a run, in the order the local benchmark writes them. A reader
#: that takes these by name rather than by position is unaffected by the order;
#: it is kept identical so the two files can be diffed by eye.
COLUMNS = (
    "run_id",
    "model",
    "tool_mode",
    "task_id",
    "capability",
    "success",
    "composite",
    "task_success",
    "protocol_compliance",
    "tool_validity",
    "error_recovery",
    "efficiency",
    "steps_used",
    "budget",
    "budget_exhausted",
    "finished",
    "tool_calls",
    "successful_tool_calls",
    "invalid_actions",
    "unstructured_tool_calls",
    "unknown_tools",
    "invalid_args",
    "tool_errors",
    "redundant_calls",
    "repeated_failed_turns",
    "recovered",
    "checks_passed",
    "checks_total",
    "latency_ms",
    "prompt_tokens",
    "completion_tokens",
)

SCHEMA_VERSION = "1"


def run_task(task: dict, profile: str, sandbox: Sandbox) -> tuple[dict, dict]:
    """Carry one task out and judge it.

    The setup runs first, the profile's behaviour runs second, and the verifier
    runs last and reads only the workspace and the closing answer. The verifier
    is never given the counters: what a solver said it did is not evidence.
    """
    task["setup"](sandbox)

    started = time.perf_counter()
    act = reference.behaviour(profile, task["id"])
    action = act(sandbox)
    checks = task["verify"](sandbox, {"answer": action["answer"], "finished": action["finished"]})
    latency_ms = round((time.perf_counter() - started) * 1000.0, 1)

    trajectory = {
        "task_id": task["id"],
        "capability": task["capability"],
        "budget": task["budget"],
        "steps_used": action["steps_used"],
        "budget_exhausted": action["steps_used"] >= task["budget"],
        "finished": action["finished"],
        "answer": action["answer"],
        "had_error": action["had_error"],
        "recovered": action["recovered"],
        "counters": action["counters"],
        "checks": checks,
        "success": all(item["passed"] for item in checks),
        "latency_ms": latency_ms,
    }
    return trajectory, scoring.score(trajectory)


def run(profile: str, suite_name: str, model: str, run_id: str) -> dict:
    """Every task of one suite, carried out and scored."""
    tasks = suite_module.suite(suite_name)
    rows: list[dict] = []
    trajectories: list[dict] = []
    scored: list[dict] = []

    for task in tasks:
        sandbox = Sandbox()
        try:
            trajectory, score = run_task(task, profile, sandbox)
        finally:
            sandbox.cleanup()
        trajectories.append(trajectory)
        scored.append(score)
        rows.append(_row(profile, run_id, model, trajectory, score))

    return {
        "profile": profile,
        "model": model,
        "run_id": run_id,
        "suite": suite_name,
        "rows": rows,
        "trajectories": trajectories,
        "scored": scored,
        "summary": scoring.aggregate(scored, trajectories),
        "capabilities": scoring.by_capability(scored, trajectories),
    }


def _row(profile: str, run_id: str, model: str, trajectory: dict, score: dict) -> dict:
    counters = trajectory["counters"]
    values = score["dimensions"]
    return {
        "run_id": run_id,
        "model": model,
        "tool_mode": profile,
        "task_id": trajectory["task_id"],
        "capability": trajectory["capability"],
        "success": 1 if trajectory["success"] else 0,
        "composite": score["composite"],
        "task_success": values["task_success"],
        "protocol_compliance": values["protocol_compliance"],
        "tool_validity": values["tool_validity"],
        "error_recovery": values["error_recovery"],
        "efficiency": values["efficiency"],
        "steps_used": trajectory["steps_used"],
        "budget": trajectory["budget"],
        "budget_exhausted": 1 if trajectory["budget_exhausted"] else 0,
        "finished": 1 if trajectory["finished"] else 0,
        "tool_calls": counters["tool_calls"],
        "successful_tool_calls": counters["successful_tool_calls"],
        "invalid_actions": counters["invalid_actions"],
        "unstructured_tool_calls": counters["unstructured_tool_calls"],
        "unknown_tools": counters["unknown_tools"],
        "invalid_args": counters["invalid_args"],
        "tool_errors": counters["tool_errors"],
        "redundant_calls": counters["redundant_calls"],
        "repeated_failed_turns": counters["repeated_failed_turns"],
        "recovered": counters["recovered"],
        "checks_passed": score["checks_passed"],
        "checks_total": score["checks_total"],
        "latency_ms": trajectory["latency_ms"],
        "prompt_tokens": 0,
        "completion_tokens": 0,
    }


def manifest(result: dict, extra: dict | None = None) -> dict:
    """The run's identity, in the shape a reader of the flat CSV already looks for."""
    body = {
        "document": "benchmark-run",
        "schema_version": SCHEMA_VERSION,
        "run": {
            "run_id": result["run_id"],
            "model": result["model"],
            "suite": result["suite"],
            "tool_mode": result["profile"],
            "tasks": len(result["rows"]),
        },
        "summary": result["summary"],
        "capabilities": result["capabilities"],
    }
    if extra:
        body.update(extra)
    return body


def write_run(out_dir: Path, result: dict, extra: dict | None = None) -> dict[str, str]:
    """Write one run's CSV and manifest beneath its own directory."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    csv_path = out_dir / "tasks.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(COLUMNS))
        writer.writeheader()
        for row in result["rows"]:
            writer.writerow({key: row[key] for key in COLUMNS})

    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest(result, extra), indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )

    return {"tasks": str(csv_path), "manifest": str(manifest_path)}


def render(result: dict) -> str:
    """The run as a table, which is what a reader looks at first."""
    summary = result["summary"]
    lines = [
        f"Benchmark run {result['run_id']}: {result['model']} over suite {result['suite']}",
        "",
        f"  {'task':<26} {'capability':<24} {'pass':<5} {'score':>6} {'steps':>5}  checks",
    ]
    for row in result["rows"]:
        lines.append(
            f"  {row['task_id']:<26} {row['capability']:<24} "
            f"{'PASS' if row['success'] else 'FAIL':<5} {row['composite']:>6.2f} "
            f"{row['steps_used']:>3}/{row['budget']:<2} {row['checks_passed']}/{row['checks_total']}"
        )

    lines += [
        "",
        f"  tasks passed        {summary['tasks_passed']}/{summary['tasks']}",
        f"  composite           {summary['composite']:.2f}",
        f"  protocol            {summary['dimensions']['protocol_compliance']:.3f}",
        f"  tool validity       {summary['dimensions']['tool_validity']:.3f}",
        f"  error recovery      {summary['dimensions']['error_recovery']:.3f} "
        f"over {summary['error_recovery_applicable_tasks']} task(s) that erred",
        f"  efficiency          {summary['dimensions']['efficiency']:.3f}",
        "",
        "  per capability",
    ]
    for capability, values in result["capabilities"].items():
        lines.append(f"    {capability:<26} {values['composite']:>6.2f}  ({values['tasks']} task(s))")

    return "\n".join(lines) + "\n"


def check(result: dict) -> list[str]:
    """Findings that would make the suite unreadable rather than merely hard.

    The suite is expected to pass under the reference profile. A task the
    reference profile fails is a broken verifier or a broken fixture, and it is
    reported as such rather than read as difficulty. Another profile failing a
    task is the measurement, not a fault, so only the reference profile's
    outcomes are held to it; every profile is held to the structure of the run.
    """
    findings: list[str] = []
    if result["profile"] == "reference":
        for row in result["rows"]:
            if not row["success"]:
                findings.append(f"the reference profile did not pass {row['task_id']}")
    if abs(sum(scoring.WEIGHTS.values()) - 1.0) > 1e-9:
        findings.append("the composite weights do not sum to one")
    expected = set(suite_module.ids(result["suite"]))
    measured = {row["task_id"] for row in result["rows"]}
    for missing in sorted(expected - measured):
        findings.append(f"the suite names {missing} and no row was written for it")
    for extra in sorted(measured - expected):
        findings.append(f"a row was written for {extra}, which the suite does not name")
    return findings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gembench.run",
        description="Run the portable benchmark over the task suite.",
    )
    parser.add_argument("--profile", default="reference", help="solver profile to run")
    parser.add_argument("--suite", default=suite_module.SUITE_ALL, help="task suite to run")
    parser.add_argument("--model", default="", help="the label the rows carry (default: <profile>-solver)")
    parser.add_argument("--run-id", default="", help="the run's directory name (default: the model label)")
    parser.add_argument("--out", default="", help="directory to write the run into; omit to print only")
    parser.add_argument("--json", action="store_true", help="print the manifest instead of the table")
    parser.add_argument("--list", action="store_true", help="list the profiles and suites and stop")
    parser.add_argument("--check", action="store_true", help="exit non-zero if the reference profile failed")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.list:
        print("profiles: " + ", ".join(reference.profile_names()))
        print("suites  : " + ", ".join(suite_module.suite_names()))
        for name in suite_module.suite_names():
            print(f"  {name}: {len(suite_module.suite(name))} task(s)")
        return 0

    model = args.model or f"{args.profile}-solver"
    run_id = args.run_id or model
    result = run(args.profile, args.suite, model, run_id)

    if args.out:
        files = write_run(Path(args.out) / run_id, result, extra={"profile": args.profile})
        print(f"wrote {files['tasks']}")
        print(f"wrote {files['manifest']}")

    if args.json:
        print(json.dumps(manifest(result), indent=2))
    else:
        print(render(result), end="")

    if args.check:
        findings = check(result)
        for finding in findings:
            print(f"[error] {finding}")
        if findings:
            return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
