#!/usr/bin/env python3
"""
XcaliburLite TUI.

A small REPL over the local model with a confined workspace navigator. The
navigator walks the workspace with ls, cd, pwd, and cat, and never leaves it: the
workspace is one folder, and the agent's reads and staged edits share that same
root, so a file the terminal green-lights is a file the model can act on. A bare
name is resolved by the interpreter: if it maps onto a real file the terminal
green-lights it and holds it for the next request. The model can read the
workspace and call exactly one tool, ``run_sh_runner``, which stages changes for
review. The dashboard is brought up alongside the prompt, and the model's
reasoning, streamed tokens, tool calls, staged edits, and lint results are shown
as they happen.

Startup runs under the splash in ``splash.py``: the banner art is revealed while
each boot step (workspace, model server, dashboard, browser) reports itself, so
the wait for the model to load is shown rather than hidden.
"""
from __future__ import annotations

import argparse
import json
import logging
import threading
import webbrowser

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.styles import Style
from rich.rule import Rule
from rich.table import Table

from config import (
    APP_NAME,
    DASHBOARD_URL,
    FIRST_PASS_MAX_TOKENS,
    FIRST_PASS_TIMEOUT,
    IGNORE_DIRS,
    JOBS_DIR,
    MAX_TOOL_ITERATIONS,
    MINIMAL_CODE_LINES,
    ORIGINAL_COPY,
    PALETTE,
    ROOT,
    SHADOW_COPY,
    SHOW_THINKING,
    TARGET_ROOT,
    TESTS_DIR,
    VERSION,
    WORKSPACE,
    WRITE_MAX_TOKENS,
)
import chat_mode
from core import adapter
from core.workspace_shell import Navigator, WorkspaceError
from dashboard.app import create_app
from dashboard.jobs import JobStore
from engine import context as context_builder
from engine import llama_server, prompts
from engine.events import Reporter
from engine.model_client import RUN_SH_RUNNER_TOOL, ModelClient, ModelError
from engine import prompt_repair
from security import sh_runner
from splash import Boot
from theme import console

# Bounds on the two corrective loops. A pass that produces no usable call is
# asked again up to this many times, which is what keeps a call cut off at the
# token ceiling from ending the turn with a file the model meant to write. The
# retries are bounded so a model that will never emit a valid call cannot spin.
_MAX_RETRIES = 3
# A write that still carries a document wrapper is asked for a smaller version
# once; after that the wrapper is stripped deterministically (prompt_repair).
_MAX_SHRINK_NUDGES = 1
_STORE = JobStore()
_NAV = Navigator()
_SELECTED: list[str] = []


class TuiReporter(Reporter):
    """Prints the model's reasoning, output, and each action as it happens."""

    def __init__(self, show_thinking: bool = SHOW_THINKING) -> None:
        self.show_thinking = show_thinking
        self._open_line = False
        self._reasoning_open = False
        self._tool_open = False
        self._tool_label = ""
        self._tool_bucket = -1

    def _newline(self) -> None:
        if self._open_line:
            console.print()
            self._open_line = False
            self._reasoning_open = False
        self._tool_open = False

    def phase(self, name: str, detail: str = "") -> None:
        self._newline()
        tail = f" [dim]{detail}[/dim]" if detail else ""
        console.print(f"[dim]:: {name}[/dim]{tail}")

    def reasoning(self, text: str) -> None:
        if not self.show_thinking:
            return
        if not self._reasoning_open:
            if self._open_line:
                console.print()
            console.print("[dim]thinking[/dim] [dim italic]", end="")
            self._reasoning_open = True
            self._open_line = True
        console.print(text, end="", markup=False, highlight=False, soft_wrap=True)

    def content(self, text: str) -> None:
        if self._reasoning_open:
            console.print()
            self._reasoning_open = False
        console.print(text, end="", markup=False, highlight=False, style="ai", soft_wrap=True)
        self._open_line = True

    def tool(self, name: str, arguments: dict) -> None:
        self._newline()
        files = arguments.get("files") or [e.get("filepath") for e in arguments.get("edits", [])]
        console.print(f"[tool]-> {name}[/tool] [dim]{', '.join(f for f in files if f)}[/dim]")

    def tool_progress(self, name: str, chars: int) -> None:
        """Show a long tool call being written, throttled to one line per bucket."""
        bucket = chars // 400
        if name == self._tool_label and bucket == self._tool_bucket:
            return
        self._tool_label = name
        self._tool_bucket = bucket
        if not self._tool_open:
            self._newline()
            console.print(f"  [dim]{name} writing call...[/dim]")
            self._tool_open = True
        console.print(f"    [dim]{chars} chars[/dim]")

    def stage(self, filepath: str, match: str, diff_lines: int) -> None:
        console.print(f"  [success]+[/success] [path]{filepath}[/path] [dim]{match} ({diff_lines} diff lines)[/dim]")

    def lint(self, filepath: str, diagnostics: list[dict]) -> None:
        console.print(f"  [warn]lint[/warn] [path]{filepath}[/path] [dim]{len(diagnostics)} finding(s)[/dim]")

    def error(self, message: str) -> None:
        self._newline()
        console.print(f"[error]{message}[/error]")


_REPORTER = TuiReporter()


def _system_prompt() -> str:
    listing = "\n".join(
        f"{entry.name}/" if entry.is_dir() else entry.name
        for entry in sorted(WORKSPACE.iterdir())
        if not entry.name.startswith(".") and entry.name not in IGNORE_DIRS
    ) or "(empty)"
    return (
        f"{prompts.AGENT_SYSTEM}\n"
        f"Workspace (the only tree you may read or edit): {WORKSPACE}\n"
        f"Current directory: {_NAV.prompt_path()}\n"
        f"Contents:\n{listing}"
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
        if edit.get("lint"):
            console.print(f"      [warn]{edit['lint'].splitlines()[0]}[/warn]")
    console.print(f"    [dim]review: [underline]{result['review_url']}[/underline][/dim]")


def _usable_tool_calls(message: dict) -> tuple[list[tuple[dict, dict]], list[tuple[dict, str]]]:
    """
    Split tool calls into usable ones and ones whose arguments will not parse.

    Arguments are reshaped by the prompt repair helper first, so a call that
    drifts from the schema is still usable. Returning the broken calls lets the
    caller ask for a retry instead of ending the turn silently.
    """
    good: list[tuple[dict, dict]] = []
    broken: list[tuple[dict, str]] = []
    for call in message.get("tool_calls") or []:
        raw = call.get("function", {}).get("arguments") or "{}"
        parsed = prompt_repair.repair_arguments(raw)
        if not parsed:
            broken.append((call, "arguments were not valid JSON"))
            continue
        normalized = prompt_repair.normalize_tool_arguments(parsed)
        if not normalized["edits"] and not normalized["run"]:
            broken.append((call, "no usable edits were present"))
            continue
        good.append((call, normalized))
    return good, broken


def _edit_is_write(edit: dict) -> bool:
    """A write when the shape says so or, definitively, when the target is absent."""
    if prompt_repair.is_write_edit(edit):
        return True
    try:
        return not adapter.resolve(edit.get("filepath", ""), TARGET_ROOT).exists()
    except adapter.AdapterError:
        return False


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


def _retry_hint(finish: str, broken: list[tuple[dict, str]]) -> str:
    """The corrective line for a pass that produced no usable tool call."""
    truncated = finish == "length"
    reason = prompt_repair.failure_reason(finish, broken[0][1] if broken else "")
    if broken and not truncated:
        return prompt_repair.shape_hint(reason)
    return prompts.tool_retry_hint(reason, truncated=truncated)


def run_turn(client: ModelClient, messages: list[dict]) -> None:
    """
    Run one user turn, executing tool calls until the model stops.

    The first pass requires a well-formed tool call, because a small model on an
    auto pass frequently narrates instead of acting. Every pass can be asked to
    write a whole file, so every pass gets a write budget; a follow-up that opens
    a second file is not squeezed into the smaller summarise budget. A pass that
    produces no usable call is a retry rather than a silent end: the reason is
    reported, the model is asked again with room to finish, and when the retries
    are spent the user is told nothing was staged. Token output is bounded on
    the first pass so a runaway generation cannot stall the prompt, and the
    stream is rendered live so the user sees the model think.
    """
    first_pass = True
    retries = 0
    shrink_nudges = 0
    fallback_instruction = _last_user_instruction(messages)
    for _ in range(MAX_TOOL_ITERATIONS):
        choice = "required" if first_pass else "auto"
        budget = FIRST_PASS_MAX_TOKENS if first_pass else WRITE_MAX_TOKENS
        deadline = FIRST_PASS_TIMEOUT if first_pass else None

        _REPORTER.phase("thinking")
        streamed = {"content": False}

        def on_delta(text: str) -> None:
            streamed["content"] = True
            _REPORTER.content(text)

        try:
            response = client.chat(
                messages, tools=[RUN_SH_RUNNER_TOOL], tool_choice=choice,
                max_tokens=budget, timeout=deadline,
                on_delta=on_delta, on_reasoning=_REPORTER.reasoning,
                on_tool_delta=_REPORTER.tool_progress,
            )
        except ModelError as exc:
            if retries < _MAX_RETRIES:
                console.print(f"[warn]{exc}; asking for a smaller, complete call[/warn]")
                messages.append({"role": "user", "content": prompts.tool_retry_hint(str(exc), truncated=True)})
                retries += 1
                continue
            _REPORTER.error(str(exc))
            return

        message = client.message_of(response)
        finish = client.finish_reason_of(response)
        content = (message.get("content") or "").strip()
        tool_calls, broken_calls = _usable_tool_calls(message)

        if not tool_calls:
            reason = prompt_repair.failure_reason(finish, broken_calls[0][1] if broken_calls else "")
            if retries < _MAX_RETRIES:
                _REPORTER.error(f"nothing was staged: {reason}; asking it to retry")
                messages.append({"role": "assistant", "content": content or None})
                messages.append({"role": "user", "content": _retry_hint(finish, broken_calls)})
                retries += 1
                continue
            _REPORTER.error(f"nothing was staged: {reason}. Name the file and ask again.")
            return

        if first_pass and shrink_nudges < _MAX_SHRINK_NUDGES:
            boilerplate = prompt_repair.boilerplate_hint(
                [edit for _call, args in tool_calls for edit in args["edits"]], is_write=_edit_is_write
            )
            if boilerplate:
                console.print("[warn]minimal-code rule: asking for a smaller file[/warn]")
                messages.append({"role": "assistant", "content": content or None})
                messages.append({"role": "user", "content": boilerplate})
                shrink_nudges += 1
                continue

        first_pass = False

        assistant_message: dict = {"role": "assistant", "content": content or None}
        if tool_calls:
            assistant_message["tool_calls"] = [call for call, _ in tool_calls]
        messages.append(assistant_message)

        if content and not streamed["content"]:
            console.print(f"[ai]{content}[/ai]")

        minimized = prompt_repair.enforce_minimal(
            [edit for _call, args in tool_calls for edit in args["edits"]], is_write=_edit_is_write
        )
        if minimized:
            console.print(f"[dim]minimal-code policy applied to {minimized} file(s)[/dim]")

        for call, arguments in tool_calls:
            function = call.get("function", {})
            if not arguments.get("instruction") and fallback_instruction:
                arguments["instruction"] = fallback_instruction
            _REPORTER.tool(function.get("name", "run_sh_runner"), arguments)
            if not arguments.get("instruction"):
                result = {
                    "success": False,
                    "error": "run_sh_runner needs a non-empty 'instruction' and 'edits'",
                }
            else:
                hint = prompt_repair.minimal_lines_hint(arguments["edits"], MINIMAL_CODE_LINES)
                if hint:
                    console.print(f"[warn]{hint}[/warn]")
                result = sh_runner.handle_tool_call(arguments, reporter=_REPORTER)
            _print_tool_result(function.get("name", "run_sh_runner"), result)
            messages.append({
                "role": "tool",
                "tool_call_id": call.get("id", ""),
                "content": json.dumps(result),
            })

    console.print("[warn]tool iteration limit reached[/warn]")


# -- workspace navigation --------------------------------------------------

def _ls(target: str = "") -> None:
    entries = _NAV.ls(target)
    if not entries:
        console.print("[dim](empty)[/dim]")
        return
    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column("name", style="path")
    table.add_column("size", style="dim", justify="right")
    for entry in entries:
        size = "" if entry.kind == "directory" else _human(entry.size)
        table.add_row(entry.name, size)
    console.print(f"[dim]{_NAV.prompt_path()}[/dim]")
    console.print(table)


def _human(size: int) -> str:
    for unit in ("B", "K", "M", "G"):
        if size < 1024:
            return f"{size:.0f}{unit}"
        size /= 1024
    return f"{size:.0f}T"


def _cat(target: str) -> None:
    text, total, truncated = _NAV.cat(target)
    console.print(text, markup=False, highlight=False, soft_wrap=True)
    if truncated:
        console.print(f"[dim]... {total} lines total; showing the first {text.count(chr(10))}[/dim]")


def _green_light(fragment: str) -> bool:
    """Resolve a name, report whether it is real, and hold it for the next request."""
    resolution = _NAV.resolve(fragment)
    if resolution.green:
        console.print(f"[success]ok[/success] [path]{resolution.path}[/path] [dim]({resolution.kind}, {resolution.confidence:.2f})[/dim]")
        if resolution.path not in _SELECTED:
            _SELECTED.append(resolution.path)
        return True
    console.print(f"[warn]no match[/warn] [dim]{resolution.describe()}[/dim]")
    return False


def _resolve_selection() -> str:
    if not _SELECTED:
        return ""
    return "\nFiles the user has confirmed exist and wants addressed:\n" + "\n".join(f"- {rel}" for rel in _SELECTED)


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


def _start_dashboard():
    """Bring the review UI up on a daemon thread and hand back the app."""
    app = create_app(start_background=True)
    from config import DASHBOARD_HOST, DASHBOARD_PORT
    # The request log would otherwise write over the live splash frame.
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    thread = threading.Thread(
        target=lambda: app.run(host=DASHBOARD_HOST, port=DASHBOARD_PORT, debug=False, use_reloader=False),
        daemon=True, name="xcalibur-dashboard",
    )
    thread.start()
    return app


# -- startup steps ---------------------------------------------------------

def _boot_workspace(_disabled: bool) -> str:
    """Create the folders the harness stages into and report where they are."""
    for folder in (WORKSPACE, ORIGINAL_COPY, SHADOW_COPY, JOBS_DIR, TESTS_DIR):
        folder.mkdir(parents=True, exist_ok=True)
    try:
        return str(WORKSPACE.relative_to(ROOT))
    except ValueError:
        return str(WORKSPACE)


def _boot_model(disabled: bool) -> str:
    """Make sure the local model server answers, starting it when necessary."""
    if disabled:
        return "disabled"
    if llama_server.ensure():
        return llama_server.base_url()
    raise RuntimeError("no answer; the model starts disabled")


def _boot_dashboard(disabled: bool) -> str:
    if disabled:
        return "disabled"
    _start_dashboard()
    return DASHBOARD_URL


def _boot_browser(open_now: bool) -> str:
    if not open_now:
        return "skipped"
    webbrowser.open(DASHBOARD_URL)
    return "opened"


def _boot(args: argparse.Namespace) -> dict:
    """Run the startup work under the splash and return each step's result."""
    boot = Boot(
        caption=f"workspace {WORKSPACE}",
        enabled=not args.no_splash,
    )
    boot.add("workspace", "workspace", lambda: _boot_workspace(False), lambda s: s)
    boot.add(
        "model", "local model server",
        lambda: _boot_model(args.no_server), lambda s: s,
    )
    boot.add(
        "dashboard", "review dashboard",
        lambda: _boot_dashboard(args.no_dashboard), lambda s: s,
    )
    boot.add(
        "browser", "opening browser",
        lambda: _boot_browser(args.open and not args.no_dashboard), lambda s: s,
    )
    return boot.run(console)


def _announce(args: argparse.Namespace, results: dict) -> None:
    """Print the two facts worth repeating once the splash has settled."""
    console.print(
        Rule(
            f"[header]{APP_NAME} {VERSION}[/header]  "
            f"[dim]workspace[/dim] [path]{WORKSPACE}[/path]",
            style=PALETTE["communicative"],
        )
    )
    if results.get("workspace") is None:
        console.print("[warn]could not create the workspace folders[/warn]")
    if results.get("model") is None and not args.no_server:
        console.print("[warn]model server unavailable; the terminal still navigates[/warn]")
    elif not args.no_server:
        console.print(f"[dim]model {results.get('model')}[/dim]")


HELP = (
    f"[dim]Workspace {WORKSPACE} - the navigator cannot leave it.[/dim]\n"
    "[bold]Navigation[/bold]  [bold]ls[/bold] [path]   [bold]cd[/bold] <dir>   "
    "[bold]pwd[/bold]   [bold]cat[/bold] <file>\n"
    "[bold]Files[/bold]       type a bare name or [bold]find[/bold] <name> to resolve it "
    "and green-light a real file; [bold]selected[/bold] lists them, [bold]drop[/bold] clears\n"
    "[bold]Chat[/bold]        [bold]/chat[/bold] talk to the model directly: no tool, no "
    "staged edits, and leaving it offers to copy\n"
    "[bold]Agent[/bold]       [bold]jobs[/bold] list staged jobs   [bold]dashboard[/bold] open the review UI\n"
    "[bold]Session[/bold]     [bold]clear[/bold] reset history   [bold]help[/bold]   [bold]quit[/bold]\n"
    "[dim]Bare text is sent to the model. The model stages changes through run_sh_runner; "
    "nothing is applied until you approve it in the dashboard. The model reads and edits "
    "only inside the workspace. Every command also accepts a leading slash.[/dim]"
)


def _command_of(line: str) -> str:
    """
    The command word of a line, with any leading slash removed.

    Every command is accepted either bare or with a leading slash, so ``chat``
    and ``/chat`` are one word rather than two code paths. A line that is only a
    slash has no command and answers empty, which the caller treats as nothing to
    do rather than as a message for the model.
    """
    return line.split(None, 1)[0].lower().lstrip("/")


def _handle_navigation(line: str) -> bool:
    """Run a navigation or file-resolution command. True when the line was consumed."""
    parts = line.split(None, 1)
    command = parts[0].lower().lstrip("/")
    rest = parts[1].strip() if len(parts) > 1 else ""

    try:
        if command in ("ls", "dir"):
            _ls(rest)
            return True
        if command == "cd":
            where = _NAV.cd(rest)
            console.print(f"[dim]{_NAV.prompt_path()}[/dim] [dim]({where})[/dim]")
            return True
        if command in ("pwd",):
            console.print(f"[path]{_NAV.prompt_path()}[/path]")
            return True
        if command == "cat":
            if not rest:
                console.print("[warn]usage: cat <file>[/warn]")
            else:
                _cat(rest)
            return True
        if command in ("find", "use", "open-file"):
            if not rest:
                console.print("[warn]usage: find <name>[/warn]")
            else:
                _green_light(rest)
            return True
        if command == "selected":
            if _SELECTED:
                for rel in _SELECTED:
                    console.print(f"[path]{rel}[/path]")
            else:
                console.print("[dim]nothing selected[/dim]")
            return True
        if command == "drop":
            _SELECTED.clear()
            console.print("[dim]selection cleared[/dim]")
            return True
    except WorkspaceError as exc:
        console.print(f"[error]{exc}[/error]")
        return True
    except adapter.AdapterError as exc:
        console.print(f"[error]{exc}[/error]")
        return True
    return False


def _maybe_resolve_bare(line: str) -> bool:
    """
    Treat a single-token line as a file lookup rather than a message.

    A name that resolves to a real file is green-lit and held; anything else
    falls through to the model, so ordinary one-word prompts still work.
    """
    if " " in line or "/" not in line and "." not in line:
        return False
    resolution = _NAV.resolve(line)
    if resolution.green and resolution.confidence >= 0.9:
        _green_light(line)
        return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=f"{APP_NAME} TUI")
    parser.add_argument("--no-server", action="store_true", help="do not start llama-server")
    parser.add_argument("--no-dashboard", action="store_true", help="do not start the dashboard")
    parser.add_argument("--no-splash", action="store_true", help="skip the startup animation")
    parser.add_argument("--open", dest="open", action="store_true", help="open the dashboard in the browser (default)")
    parser.add_argument("--no-open", dest="open", action="store_false", help="do not open the dashboard")
    parser.set_defaults(open=True)
    args = parser.parse_args()

    results = _boot(args)
    _announce(args, results)
    console.print(HELP)

    client = ModelClient()
    session: PromptSession = PromptSession(
        history=InMemoryHistory(),
        style=Style.from_dict({"prompt": f"{PALETTE['communicative']} bold"}),
    )
    messages: list[dict] = [{"role": "system", "content": _system_prompt()}]

    while True:
        label = _NAV.prompt_path()
        try:
            raw = session.prompt(HTML(f"<b><style fg='{PALETTE['communicative']}'>{label}</style></b> &gt; "))
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Goodbye.[/dim]")
            break

        line = raw.strip()
        if not line:
            continue
        # A leading slash is accepted on every command, so /chat and chat are the
        # same word and the chat-mode commands are spelled one way everywhere.
        command = _command_of(line)
        if not command:
            continue

        if command in ("quit", "exit", "q"):
            console.print("[dim]Goodbye.[/dim]")
            break
        if command == "help":
            console.print(HELP)
            continue
        if command == "jobs":
            _print_jobs()
            continue
        if command in ("dashboard", "browser"):
            webbrowser.open(DASHBOARD_URL)
            console.print(f"[dim]opening {DASHBOARD_URL}[/dim]")
            continue
        if command == "clear":
            messages = [{"role": "system", "content": _system_prompt()}]
            console.print("[dim]history cleared[/dim]")
            continue
        if command in ("chat", "talk"):
            # Direct chat owns its own history and its own prompt, so the agent's
            # conversation and its staged jobs are exactly as they were on return.
            chat_mode.run(client, PALETTE["communicative"])
            continue
        if _handle_navigation(line):
            continue
        if _maybe_resolve_bare(line):
            continue

        augmented, attached, labels = context_builder.build_user_context(
            line, TARGET_ROOT, cwd=_NAV.cwd
        )
        for rel, label in labels.items():
            console.print(f"[success]ok[/success] [path]{rel}[/path] [dim]({label})[/dim]")
        if attached:
            console.print(f"[dim]attached {len(attached)} file(s) from your message[/dim]")
        selection = _resolve_selection()
        if selection:
            augmented += selection
        messages.append({"role": "user", "content": augmented})
        run_turn(client, messages)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
