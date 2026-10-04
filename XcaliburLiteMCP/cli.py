#!/usr/bin/env python3
"""
XcaliburLite TUI.

A small REPL over the local model. The model can read the workspace and call
exactly one tool, ``run_sh_runner``, which stages changes for review. The
dashboard is brought up alongside the prompt at a fixed URL.
"""
from __future__ import annotations

import argparse
import json
import threading
import webbrowser

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.styles import Style
from rich.rule import Rule
from rich.table import Table

from config import (
    DASHBOARD_URL,
    FIRST_PASS_MAX_TOKENS,
    FIRST_PASS_TIMEOUT,
    GENERATION_MAX_TOKENS,
    MAX_TOOL_ITERATIONS,
    PALETTE,
    RETRY_MAX_TOKENS,
    TARGET_ROOT,
    WORKSPACE,
)
from dashboard.app import create_app
from dashboard.jobs import JobStore
from engine import context as context_builder
from engine import llama_server, prompts
from engine.model_client import RUN_SH_RUNNER_TOOL, ModelClient, ModelError
from security import sh_runner
from theme import console

APP_NAME = "XcaliburLite"
_MAX_CORRECTIONS = 1
_STORE = JobStore()


def _system_prompt() -> str:
    listing = "\n".join(
        f"{entry.name}/" if entry.is_dir() else entry.name
        for entry in sorted(TARGET_ROOT.iterdir())
        if not entry.name.startswith(".")
    ) or "(empty)"
    return (
        f"{prompts.AGENT_SYSTEM}\n"
        f"Target root: {TARGET_ROOT}\n"
        f"Top level:\n{listing}"
    )


def _print_tool_result(name: str, result: dict) -> None:
    if not result.get("success"):
        console.print(f"  [error]x[/error] {name}: {result.get('error', 'failed')}")
        return
    console.print(
        f"  [success]+[/success] staged [tool]{result['job_id']}[/tool] "
        f"[dim]status={result['status']} edits={len(result.get('edits', []))}[/dim]"
    )
    for edit in result.get("edits", []):
        console.print(f"    [path]{edit['filepath']}[/path] [dim]{edit['match']} ({edit['diff_lines']} diff lines)[/dim]")
    console.print(f"    [dim]review: [underline]{result['review_url']}[/underline][/dim]")


def _usable_tool_calls(message: dict) -> tuple[list[dict], list[tuple[dict, str]]]:
    """
    Split tool calls into usable ones and ones whose arguments will not parse.

    A truncated or malformed argument blob used to be dropped silently, which
    ended the turn with the model believing it had acted. Returning the broken
    calls lets the caller ask for a retry instead.
    """
    good: list[dict] = []
    broken: list[tuple[dict, str]] = []
    for call in message.get("tool_calls") or []:
        raw = call.get("function", {}).get("arguments") or "{}"
        try:
            json.loads(raw)
        except json.JSONDecodeError as exc:
            broken.append((call, str(exc)))
            continue
        good.append(call)
    return good, broken


def _last_user_instruction(messages: list[dict]) -> str:
    """
    The most recent human request, with any attached file context stripped.

    Used as the ``instruction`` when the model calls the tool without one. The
    instruction is human-facing metadata, so the user's own words are a faithful
    substitute and let the turn still stage instead of failing.
    """
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        text = message.get("content") or ""
        marker = "\n\nAttached files ("
        if marker in text:
            text = text.split(marker, 1)[0]
        return text.strip()
    return ""


def run_turn(client: ModelClient, messages: list[dict]) -> None:
    """
    Run one user turn, executing tool calls until the model stops.

    The first pass requires a well-formed tool call, because a small model on an
    auto pass frequently narrates instead of acting. When the model returns
    nothing usable it gets a bounded corrective nudge rather than the turn
    ending silently. Token output is bounded on the first pass so a runaway
    generation cannot stall the prompt.
    """
    first_pass = True
    corrections = 0
    fallback_instruction = _last_user_instruction(messages)
    for _ in range(MAX_TOOL_ITERATIONS):
        choice = "required" if first_pass else "auto"
        if first_pass:
            # First try stays tight for speed; after a nudge the model gets the
            # generous retry budget so an over-produced file can still complete.
            budget = RETRY_MAX_TOKENS if corrections else FIRST_PASS_MAX_TOKENS
            deadline = FIRST_PASS_TIMEOUT
        else:
            budget = GENERATION_MAX_TOKENS
            deadline = None
        try:
            response = client.chat(
                messages, tools=[RUN_SH_RUNNER_TOOL], tool_choice=choice,
                max_tokens=budget, timeout=deadline,
            )
        except ModelError as exc:
            # A first pass that runs out of time or budget is usually an
            # oversized file. Ask once for a smaller, complete call before
            # giving up, so the turn does not end without acting.
            if first_pass and corrections < _MAX_CORRECTIONS:
                console.print(f"[warn]{exc}; asking for a smaller, complete call[/warn]")
                messages.append({"role": "user", "content": prompts.tool_retry_hint(str(exc), truncated=True)})
                corrections += 1
                continue
            console.print(f"[error]{exc}[/error]")
            return

        message = client.message_of(response)
        finish = client.finish_reason_of(response)
        content = (message.get("content") or "").strip()
        tool_calls, broken_calls = _usable_tool_calls(message)

        if first_pass and not tool_calls and corrections < _MAX_CORRECTIONS:
            truncated = finish == "length"
            if broken_calls:
                hint = prompts.tool_retry_hint(broken_calls[0][1], truncated=truncated)
            else:
                hint = prompts.TOOL_RETRY_HINT
                if truncated:
                    hint = prompts.tool_retry_hint("the response was cut off", truncated=True)
            console.print("[warn]model did not call the tool; asking it to retry[/warn]")
            messages.append({"role": "assistant", "content": content or None})
            messages.append({"role": "user", "content": hint})
            corrections += 1
            continue

        first_pass = False

        assistant_message: dict = {"role": "assistant", "content": content or None}
        if tool_calls:
            assistant_message["tool_calls"] = tool_calls
        messages.append(assistant_message)

        if content:
            console.print(f"[ai]{content}[/ai]")

        if not tool_calls:
            return

        console.print(Rule("[tool]run_sh_runner[/tool]", style="#00E1FF", characters="-"))
        for call in tool_calls:
            function = call.get("function", {})
            try:
                arguments = json.loads(function.get("arguments") or "{}")
            except json.JSONDecodeError:
                arguments = {}
            if not arguments.get("instruction") and fallback_instruction:
                arguments["instruction"] = fallback_instruction
            if not arguments.get("instruction"):
                result = {
                    "success": False,
                    "error": "run_sh_runner needs a non-empty 'instruction' and 'edits'",
                }
            else:
                result = sh_runner.handle_tool_call(arguments)
            _print_tool_result(function.get("name", "run_sh_runner"), result)
            messages.append({
                "role": "tool",
                "tool_call_id": call.get("id", ""),
                "content": json.dumps(result),
            })

    console.print("[warn]tool iteration limit reached[/warn]")


def _print_jobs() -> None:
    jobs = _STORE.list()
    if not jobs:
        console.print("[dim]No jobs yet.[/dim]")
        return
    table = Table(show_header=True, header_style=f"bold {PALETTE['communicative']}")
    table.add_column("job id", style="cyan")
    table.add_column("status")
    table.add_column("files")
    table.add_column("url", style="dim")
    for job in jobs[:20]:
        table.add_row(job.id, job.status, ", ".join(job.files) or "-", f"{DASHBOARD_URL}/job/{job.id}")
    console.print(table)


def _start_dashboard() -> None:
    app = create_app(start_background=True)
    from config import DASHBOARD_HOST, DASHBOARD_PORT
    thread = threading.Thread(
        target=lambda: app.run(host=DASHBOARD_HOST, port=DASHBOARD_PORT, debug=False, use_reloader=False),
        daemon=True, name="xcalibur-dashboard",
    )
    thread.start()


HELP = (
    "[bold]help[/bold] commands   [bold]jobs[/bold] list staged jobs   "
    "[bold]open[/bold] dashboard   [bold]clear[/bold] reset   [bold]quit[/bold] exit\n"
    "[dim]Bare text is sent to the model. The model stages changes through run_sh_runner; "
    "nothing is applied until you approve it in the dashboard.[/dim]"
)


def main() -> int:
    parser = argparse.ArgumentParser(description="XcaliburLite TUI")
    parser.add_argument("--no-server", action="store_true", help="do not start llama-server")
    parser.add_argument("--no-dashboard", action="store_true", help="do not start the dashboard")
    parser.add_argument("--open", dest="open", action="store_true", help="open the dashboard in the browser (default)")
    parser.add_argument("--no-open", dest="open", action="store_false", help="do not open the dashboard")
    parser.set_defaults(open=True)
    args = parser.parse_args()

    WORKSPACE.mkdir(parents=True, exist_ok=True)

    if not args.no_server:
        console.print("[dim]checking local model server...[/dim]")
        try:
            if llama_server.ensure():
                console.print(f"[success]model server ready[/success] [dim]{llama_server.base_url()}[/dim]")
            else:
                console.print("[warn]model server did not come up; starts disabled[/warn]")
        except Exception as exc:  # noqa: BLE001 - the message is the report
            console.print(f"[error]{exc}[/error]")

    if not args.no_dashboard:
        _start_dashboard()
        console.print(f"[success]dashboard[/success] [dim]{DASHBOARD_URL}[/dim]")
        if args.open:
            webbrowser.open(DASHBOARD_URL)

    console.print(Rule(f"[header]{APP_NAME}[/header]  [path]{TARGET_ROOT}[/path]", style="#00E1FF"))
    console.print(HELP)

    client = ModelClient()
    session: PromptSession = PromptSession(
        history=InMemoryHistory(),
        style=Style.from_dict({"prompt": "#00E1FF bold"}),
    )
    messages: list[dict] = [{"role": "system", "content": _system_prompt()}]

    while True:
        try:
            raw = session.prompt(HTML("<b><style fg='#00E1FF'>xcalibur</style></b> &gt; "))
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Goodbye.[/dim]")
            break

        line = raw.strip()
        if not line:
            continue
        command = line.split(None, 1)[0].lower()

        if command in ("quit", "exit", "q"):
            console.print("[dim]Goodbye.[/dim]")
            break
        if command == "help":
            console.print(HELP)
            continue
        if command == "jobs":
            _print_jobs()
            continue
        if command == "open":
            webbrowser.open(DASHBOARD_URL)
            console.print(f"[dim]opening {DASHBOARD_URL}[/dim]")
            continue
        if command == "clear":
            messages = [{"role": "system", "content": _system_prompt()}]
            console.print("[dim]history cleared[/dim]")
            continue

        augmented, attached = context_builder.build_user_context(line, TARGET_ROOT)
        if attached:
            console.print(f"[dim]attached {len(attached)} file(s): {', '.join(attached)}[/dim]")
        messages.append({"role": "user", "content": augmented})
        run_turn(client, messages)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
