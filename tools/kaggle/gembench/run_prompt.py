"""Run one prompt-protocol endpoint over the portable suite.

This is the caller `prompt_agent` was written for, and the sibling of
`run_hybrid.py`. Where that one measures a merged planner-coder answering with
structured tool calls, this one measures a single model answering with the one
JSON object the harness loop reads. Both write through `runner.py`, so the two
land in the same table and the same columns, and a row from either can be read
beside `naive-solver` and `reference-solver` without a special case.

Nothing here is specific to one machine: the endpoint is a URL, the output is a
directory and the suite is a name. The same command that measures a quant on a
rented card measures it on a laptop.

    python3 tools/kaggle/gembench/run_prompt.py \\
        --url http://127.0.0.1:8793 \\
        --model gemma-4-E2B-it-agent-Q4_K_M \\
        --out results/benchmark/kaggle \\
        --check
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if __package__ in (None, ""):
    sys.path.insert(0, str(HERE.parent))

from gembench import prompt_agent, runner  # noqa: E402
from gembench import suite as suite_module  # noqa: E402

DEFAULT_PROFILE = "gemma-prompt"
DEFAULT_URL = "http://127.0.0.1:8793"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gembench.run_prompt",
        description="Measure one prompt-protocol model endpoint against the task suite.",
    )
    parser.add_argument(
        "--url",
        default=DEFAULT_URL,
        help=(
            "base URL of the model's server, or the completions endpoint "
            "(both mean one thing; the chat path is appended)"
        ),
    )
    parser.add_argument("--model", default="", help="the label the rows carry")
    parser.add_argument("--served-model", default="", help="the model name to send, when the server wants one")
    parser.add_argument("--profile", default=DEFAULT_PROFILE, help="profile name to register the solver under")
    parser.add_argument("--suite", default=suite_module.SUITE_ALL, help="task suite to run")
    parser.add_argument("--run-id", default="", help="the run's directory name (default: the model label)")
    parser.add_argument("--out", default="", help="directory to write the run into; omit to print only")
    parser.add_argument("--timeout", type=float, default=300.0, help="seconds for one model turn")
    parser.add_argument(
        "--max-tokens", type=int, default=prompt_agent.DEFAULT_MAX_TOKENS, help="cap on one turn's reply"
    )
    parser.add_argument(
        "--max-observation",
        type=int,
        default=prompt_agent.DEFAULT_MAX_OBSERVATION,
        help="the longest observation fed back to the solver",
    )
    parser.add_argument(
        "--decoder",
        choices=prompt_agent.DECODERS,
        default=prompt_agent.DECODER_NONE,
        help="the engine-side constraint to send, named as the harness names it",
    )
    parser.add_argument("--quant", default="", help="a label for the weights under test, recorded with the run")
    parser.add_argument("--skip-probe", action="store_true", help="run without asking /health first")
    parser.add_argument("--json", action="store_true", help="print the manifest instead of the table")
    parser.add_argument("--check", action="store_true", help="exit non-zero on an unreadable run")
    return parser


def write_transcripts(run_dir: Path, client, result: dict) -> Path:
    """Write every turn of every task beside the run.

    One JSON line per turn, carrying the task, what the model wrote and what it
    was told next. The CSV says a turn was unstructured; only the turn itself
    says whether that was prose, a fenced block or a call to a tool that does
    not exist, and those are three different findings about a post-train.
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "transcripts.jsonl"
    summaries = {row["task_id"]: row for row in result["rows"]}
    solver = getattr(client, "solver", None)
    transcripts = getattr(solver, "transcripts", {}) if solver is not None else {}

    with path.open("w", encoding="utf-8") as handle:
        for task_id, turns in transcripts.items():
            row = summaries.get(task_id, {})
            for turn in turns:
                handle.write(
                    json.dumps(
                        {
                            "task_id": task_id,
                            "capability": row.get("capability", ""),
                            "passed": row.get("success"),
                            "steps_used": row.get("steps_used"),
                            "budget": row.get("budget"),
                            **turn,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
    return path


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    client = prompt_agent.register(
        args.profile,
        url=args.url,
        model=args.served_model,
        timeout=args.timeout,
        max_tokens=args.max_tokens,
        max_observation=args.max_observation,
        decoder=args.decoder,
    )

    if not args.skip_probe and not client.health():
        print(f"[error] the model at {client.base_url} is not answering /health", file=sys.stderr)
        return 3

    model = args.model or f"{args.profile}-solver"
    run_id = args.run_id or model
    # The decoder is part of the condition, so it is part of the run's name. Two
    # runs of the same weights under different decoders are two measurements and
    # must not land in the same directory.
    if args.decoder != prompt_agent.DECODER_NONE and not run_id.endswith(args.decoder):
        run_id = f"{run_id}-{args.decoder}"
    result = runner.run(args.profile, args.suite, model, run_id)

    extra = {
        "profile": args.profile,
        # The address the run was actually taken against, not the spelling that
        # was typed. A manifest that records the input verbatim cannot say which
        # server answered when the two spellings were once two different urls.
        "service": client.base_url,
        "protocol": "prompt",
        "decoder": args.decoder,
        "quant": args.quant,
        "max_tokens": args.max_tokens,
        "max_observation": args.max_observation,
        "shell_bridge": "documented-commands",
        "prompt_tokens": client.prompt_tokens,
        "completion_tokens": client.completion_tokens,
        "turns": client.turns,
    }
    if client.grammar:
        extra["grammar_sha256"] = hashlib.sha256(client.grammar.encode("utf-8")).hexdigest()

    if args.out:
        files = runner.write_run(Path(args.out) / run_id, result, extra=extra)
        print(f"wrote {files['tasks']}")
        print(f"wrote {files['manifest']}")
        transcripts = write_transcripts(Path(args.out) / run_id, client, result)
        print(f"wrote {transcripts}")

    if args.json:
        print(json.dumps(runner.manifest(result, extra), indent=2))
    else:
        print(runner.render(result), end="")

    if args.check:
        findings = runner.check(result)
        for finding in findings:
            print(f"[error] {finding}")
        if findings:
            return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
