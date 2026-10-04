#!/usr/bin/env python3
"""
The MCP server -- XcaliburLite behind a stdio transport.

An MCP host starts this file, speaks JSON-RPC 2.0 over standard input and output
one message per line, and gets the same surface the terminal has: the adapter
finds, lists and reads files under the workspace, and a single write tool,
``run_sh_runner``, stages proposed changes into the shadow copy for approval. The
server never writes to the live tree, because the runner it delegates to cannot
and nothing here writes a file at all.

Standard output carries protocol messages and nothing else. Every diagnostic
goes to standard error, because a host that cannot parse a line has lost the
session, and a stray print would do it.

Reads are confined to the workspace by the adapter, so a path that escapes it
is refused with a reason rather than resolved. A file larger than one prompt
window is returned one window at a time: the first window is handed back with the
window list, and the caller asks again with a line range.

Usage:
    python3 mcp_server.py               # serve on stdio
    python3 mcp_server.py --tools       # print the tool list as JSON and exit
    python3 mcp_server.py --self-check  # offline check of the handshake and reads
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config import APP_NAME, CHUNK_MAX_CHARS, HARNESS_DIR, MAX_ROWS, TARGET_ROOT, VERSION  # noqa: E402
from core import adapter, file_chunker  # noqa: E402
from engine.events import NULL_REPORTER  # noqa: E402
from security import sh_runner  # noqa: E402

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = APP_NAME.lower()
# One version, reported by the handshake and stamped into the banner.
SERVER_VERSION = VERSION

INSTRUCTIONS = (
    "XcaliburLite stages code changes for human review. Use the read tools to "
    "locate what you need, then call run_sh_runner with an instruction and the "
    "precise edits. Nothing is applied until a person approves the staged change "
    "in the review dashboard."
)

READ_TOOLS: list[dict] = [
    {
        "name": "read_file",
        "description": (
            "Read a file under the workspace as text. A file larger than one "
            "prompt window is returned one overlapping window at a time; pass a "
            "start_line and end_line to read a later window."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to the workspace."},
                "start_line": {"type": "integer", "description": "First line to read, 1-indexed."},
                "end_line": {"type": "integer", "description": "Last line to read, inclusive."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "list_files",
        "description": "List the files under a directory of the workspace.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory relative to the workspace; empty for the root."},
                "limit": {"type": "integer", "description": f"Maximum entries to return, up to {MAX_ROWS}."},
            },
            "required": [],
        },
    },
    {
        "name": "find_files",
        "description": "Find files under the workspace whose path matches a glob pattern.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern, for example '**/*.py'."},
                "limit": {"type": "integer", "description": f"Maximum matches to return, up to {MAX_ROWS}."},
            },
            "required": ["pattern"],
        },
    },
]


def _write_tool() -> dict:
    """The staging tool, shaped for MCP from the schema the model itself is given."""
    from engine.model_client import RUN_SH_RUNNER_TOOL

    function = RUN_SH_RUNNER_TOOL["function"]
    return {
        "name": function["name"],
        "description": function["description"],
        "inputSchema": function["parameters"],
    }


def tool_list() -> list[dict]:
    return [*READ_TOOLS, _write_tool()]


def _bounded(value: Any, default: int, ceiling: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(number, ceiling))


def _call_read_file(arguments: dict) -> dict:
    rel = str(arguments.get("path", "")).strip()
    if not rel:
        return {"error": "path is required"}
    started = arguments.get("start_line")
    ended = arguments.get("end_line")
    try:
        read = adapter.read_text(
            rel, start_line=started or None, end_line=ended or None, root=TARGET_ROOT,
        )
    except adapter.AdapterError as exc:
        return {"error": str(exc)}
    if len(read.content) <= CHUNK_MAX_CHARS:
        return {
            "path": read.rel_path,
            "lines": read.total_lines,
            "text": read.content,
        }
    windows = file_chunker.chunk_lines(read.content)
    first = windows[0]
    return {
        "path": read.rel_path,
        "lines": read.total_lines,
        "window": first.label,
        "windows": [chunk.label for chunk in windows],
        "text": first.text,
        "note": "file exceeds one window; ask again with start_line and end_line for a later window",
    }


def _call_list_files(arguments: dict) -> dict:
    rel = str(arguments.get("path", "")).strip()
    try:
        target = adapter.resolve(rel, TARGET_ROOT) if rel else TARGET_ROOT
    except adapter.AdapterError as exc:
        return {"error": str(exc)}
    if not target.exists() or not target.is_dir():
        return {"error": f"not a directory: {rel or '.'}"}
    entries = adapter.list_tree(target, limit=_bounded(arguments.get("limit"), MAX_ROWS, MAX_ROWS))
    return {
        "path": adapter.relative(target, TARGET_ROOT),
        "count": len(entries),
        "files": [{"path": entry.rel_path, "bytes": entry.size} for entry in entries],
    }


def _call_find_files(arguments: dict) -> dict:
    pattern = str(arguments.get("pattern", "")).strip()
    if not pattern:
        return {"error": "pattern is required"}
    if pattern.startswith("/") or ".." in Path(pattern).parts:
        return {"error": "pattern must stay inside the workspace"}
    matches = adapter.find_files(pattern, root=TARGET_ROOT, limit=_bounded(arguments.get("limit"), MAX_ROWS, MAX_ROWS))
    return {
        "pattern": pattern,
        "count": len(matches),
        "files": [{"path": entry.rel_path, "bytes": entry.size} for entry in matches],
    }


def call_tool(name: str, arguments: dict) -> dict:
    """
    Run one tool and return a JSON-serialisable payload.

    Every tool returns a payload shaped for the model. ``run_sh_runner`` returns
    the runner's own result, which already carries ``success``, the job id and
    the review URL, so a failure is reported the same way a success is instead of
    arriving as a transport error.
    """
    if name == "run_sh_runner":
        return sh_runner.handle_tool_call(arguments, reporter=NULL_REPORTER)
    if name == "read_file":
        return _call_read_file(arguments)
    if name == "list_files":
        return _call_list_files(arguments)
    if name == "find_files":
        return _call_find_files(arguments)
    return {"error": f"unknown tool: {name}"}


def _error(request_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _result(request_id: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def handle_message(message: dict) -> dict | None:
    """Answer one JSON-RPC message. A notification yields None, which sends nothing."""
    method = message.get("method")
    request_id = message.get("id")
    params = message.get("params") or {}
    if not isinstance(params, dict):
        params = {}
    notification = "id" not in message

    if method == "initialize":
        requested = params.get("protocolVersion")
        return _result(request_id, {
            "protocolVersion": requested or PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": INSTRUCTIONS,
        })
    if method in ("notifications/initialized", "initialized"):
        return None
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {"tools": tool_list()})
    if method == "tools/call":
        name = str(params.get("name", ""))
        arguments = params.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
        if not name:
            return _error(request_id, -32602, "tools/call needs a tool name")
        payload = call_tool(name, arguments)
        failed = payload.get("success") is False or ("error" in payload and "success" not in payload)
        return _result(request_id, {
            "content": [{"type": "text", "text": json.dumps(payload, indent=2)}],
            "structuredContent": payload,
            "isError": bool(failed),
        })
    if method in ("resources/list", "prompts/list"):
        return _result(request_id, {method.split("/")[0]: []})
    if notification:
        return None
    return _error(request_id, -32601, f"method not found: {method}")


def serve(stdin=None, stdout=None) -> int:
    """Read newline-delimited JSON-RPC from stdin, answer on stdout, until EOF."""
    source = stdin or sys.stdin
    sink = stdout or sys.stdout
    for raw in source:
        line = raw.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            print(f"xcalibur: dropped an unparsable line ({len(line)} bytes)", file=sys.stderr)
            continue
        if not isinstance(message, dict):
            print("xcalibur: dropped a message that is not an object", file=sys.stderr)
            continue
        try:
            response = handle_message(message)
        except Exception as exc:  # noqa: BLE001 - the host must get an answer, not a stack
            response = _error(message.get("id"), -32603, f"internal error: {exc}")
        if response is None:
            continue
        sink.write(json.dumps(response) + "\n")
        sink.flush()
    return 0


def _sample_relative_file() -> tuple[str | None, Path | None]:
    """
    A readable file inside the workspace, for the read check.

    The check must not assume a particular tree. A configured workspace may hold
    the user's project or nothing at all, so an empty one gets a small probe file
    inside the harness store, which is still inside the workspace as far as the
    read path is concerned. Returns (relative path, temporary file to remove).
    """
    for pattern in ("**/*.py", "**/*.txt", "**/*"):
        found = adapter.find_files(pattern, root=TARGET_ROOT, limit=1)
        if found:
            return found[0].rel_path, None
    HARNESS_DIR.mkdir(parents=True, exist_ok=True)
    probe = HARNESS_DIR / "selfcheck.txt"
    probe.write_text("xcalibur self-check\n", encoding="utf-8")
    return adapter.relative(probe, TARGET_ROOT), probe


def self_check(stdout=None) -> int:
    """
    Exercise the handshake and the reads without a model or a network.

    A host that cannot complete this exchange has a transport problem, not a
    model problem, and this is the shortest way to tell the two apart.
    """
    sink = stdout or sys.stdout
    checks: list[tuple[str, bool, str]] = []

    handshake = handle_message({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    ok = handshake is not None and "result" in handshake and handshake["result"]["protocolVersion"]
    checks.append(("initialize", bool(ok), handshake["result"]["serverInfo"]["name"] if ok else "no result"))

    listed = handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = [tool["name"] for tool in listed["result"]["tools"]] if listed else []
    checks.append(("tools/list", "run_sh_runner" in names, ", ".join(names)))

    initialized = handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"})
    checks.append(("notification yields no reply", initialized is None, ""))

    sample, temporary = _sample_relative_file()
    try:
        if sample is None:
            checks.append(("read_file", False, "the workspace holds no readable file"))
        else:
            read = call_tool("read_file", {"path": sample})
            checks.append(("read_file", "error" not in read, read.get("path", read.get("error", ""))))
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

    escape = call_tool("read_file", {"path": "../../etc/passwd"})
    checks.append(("read_file refuses an escape", "error" in escape, escape.get("error", "")))

    unknown = handle_message({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "nope"}})
    refused = unknown is not None and unknown["result"]["isError"] is True
    checks.append(("unknown tool is an error, not a crash", refused, ""))

    failed = [name for name, passed, _ in checks if not passed]
    for name, passed, detail in checks:
        mark = "ok" if passed else "FAIL"
        sink.write(f"{mark:>4}  {name}  {detail}\n")
    sink.write(f"{len(checks) - len(failed)} passed, {len(failed)} failed\n")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="XcaliburLite MCP stdio server")
    parser.add_argument("--tools", action="store_true", help="print the tool list as JSON and exit")
    parser.add_argument("--self-check", action="store_true", help="check the handshake and reads, then exit")
    args = parser.parse_args(argv)

    if args.tools:
        print(json.dumps({"tools": tool_list()}, indent=2))
        return 0
    if args.self_check:
        return self_check()
    return serve()


if __name__ == "__main__":
    raise SystemExit(main())
