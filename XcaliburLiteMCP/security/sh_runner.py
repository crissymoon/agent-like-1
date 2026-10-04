"""
The shell runner -- the only script the agent may trigger.

It accepts one request (a JSON object), scans the request for anything unsafe,
and hands it to the orchestrator, which stages the change into a shadow copy
and queues it for human review. Nothing here writes to the live tree.

Usage:
    python -m security.sh_runner --request request.json
    python -m security.sh_runner --stdin
    python -m security.sh_runner --scan "pytest -q"
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running as a loose script (python security/sh_runner.py).
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config import TARGET_ROOT  # noqa: E402
from engine import orchestrator  # noqa: E402
from engine.events import NULL_REPORTER, Reporter  # noqa: E402
from security import command_guard  # noqa: E402


def _load_request(args: argparse.Namespace) -> dict:
    if args.request:
        return json.loads(Path(args.request).read_text(encoding="utf-8"))
    if args.stdin:
        return json.loads(sys.stdin.read())
    raise SystemExit("provide --request <file> or --stdin")


def handle_tool_call(arguments: dict, cwd: Path | None = None,
                     reporter: Reporter = NULL_REPORTER) -> dict:
    """
    Execute the agent's run_sh_runner call. Returns the JSON-serialisable
    payload that is fed back to the model.
    """
    result = orchestrator.handle_request(arguments, root=TARGET_ROOT, reporter=reporter)
    return orchestrator.tool_result(result)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="XcaliburLite guarded shell runner")
    parser.add_argument("--request", help="path to a JSON request file")
    parser.add_argument("--stdin", action="store_true", help="read the JSON request from stdin")
    parser.add_argument("--scan", help="scan a single command with the guard and exit")
    args = parser.parse_args(argv)

    if args.scan:
        allowed, reason = command_guard.scan(args.scan)
        print(json.dumps({"allowed": allowed, "reason": reason}))
        return 0 if allowed else 2

    request = _load_request(args)
    result = orchestrator.handle_request(request, root=TARGET_ROOT)
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
