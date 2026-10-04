"""A live solver that drives the merged Q2 planner-coder service.

The artifact this measures is a service rather than a file. A T5 planner is
served on one port and a llama.cpp coder on another, and `q2_hybrid_server.py`
sits in front of both behind the OpenAI chat-completions shape. A turn that
produces a plan comes back as `tool_calls` and no text; a turn that produces
none is the coder's closing answer. That is exactly the shape a tool-calling
harness already drives, so this module is the loop around it: send the goal,
execute what comes back inside the sandbox, feed the observations in, and stop
when a turn carries text instead of calls.

Nothing here holds weights, names a machine or imports a third party. The
service is addressed by URL and the tools are the same names the fixtures and
the local runtime use, so a run this module produces is written by `runner.py`
and read by `compare.py` with no special case, and the row it lands in is
directly comparable with `naive-solver` and `reference-solver`.

The profile is registered rather than hardwired: `reference.PROFILES` holds the
two deterministic fixtures, and `register` adds the live table beside them
through the one extension point `reference` exposes. Importing this module
registers nothing, so the notebook, which embeds `reference` and has no
network, is unaffected.
"""

from __future__ import annotations

import json
from urllib import error as url_error
from urllib import request as url_request

try:  # the package on a machine
    from . import endpoint
    from . import reference
    from .sandbox import Sandbox, SandboxError
except ImportError:  # a notebook cell or a direct script
    import endpoint  # type: ignore
    import reference  # type: ignore
    from sandbox import Sandbox, SandboxError  # type: ignore


class HybridError(RuntimeError):
    """The merged service cannot answer, or answered something unusable."""


class HybridUnreachable(HybridError):
    """The merged service was never reached, which is not a solver failure."""


#: Every tool a call may name. A name outside this set is not an argument error
#: and not a runtime error: it is a call the solver imagined, and it is counted
#: as one so the imagined name is visible rather than folded into tool errors.
TOOL_NAMES = frozenset(
    {
        "read_file",
        "write_file",
        "append_file",
        "edit_file",
        "delete_file",
        "move_file",
        "make_directory",
        "list_files",
        "search_files",
        "grep_files",
        "get_file_info",
        "run_command",
        "restore_file",
    }
)

#: The tools the sandbox cannot carry out. The portable workspace has files and
#: no shell, so a plan that asks for one is refused explicitly rather than
#: reported as an unknown tool: the model knew the tool and the harness cannot
#: run it, and those are different facts about the run.
UNAVAILABLE_TOOLS = frozenset({"run_command", "restore_file"})

#: The counters a solver record carries, mirroring `reference._DEFAULT_COUNTERS`
#: so the two produce rows of the same shape.
_COUNTERS = (
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
)

#: The system turn sent ahead of every goal. It names the tool set and the three
#: sections, because a model told the shape and shown it in training has one
#: signal rather than two. The planner reads the first user turn, so this text
#: is for the record and for any coder that reads it.
SYSTEM_PROMPT = (
    "You are the planning half of a coding agent. Answer with three sections "
    "and nothing else: ### Reasoning, ### Tools and ### Response. Each line of "
    "### Tools is one tool call: a JSON object with a name and an arguments "
    "object, inside <tool_call> tags. ### Response is the closing text. "
    "The tools are: read_file(filepath), write_file(filepath, content), "
    "append_file(filepath, content), edit_file(filepath, old, new), "
    "delete_file(filepath), move_file(source, destination), "
    "make_directory(directory), list_files(directory, recursive), "
    "search_files(pattern), grep_files(pattern), get_file_info(filepath)."
)


# ---------------------------------------------------------------------------
# The transport.
# ---------------------------------------------------------------------------


class Client:
    """One address in front of the merged service.

    The client is deliberately thin: it sends the message list and returns the
    decoded body. Every decision about what a turn means is made by the loop,
    so the body is the only thing this class knows how to shape.
    """

    def __init__(
        self,
        base_url: str,
        model: str = "q2-planner-coder",
        timeout: float = 300.0,
        probe_timeout: float = 3.0,
    ) -> None:
        #: The service's root, from whichever spelling of its url was handed in.
        #: Both live profiles take the same argument from the same documented
        #: command lines, one of which is the completions endpoint, so the
        #: address is reconciled once here rather than at each call site.
        self.base_url = endpoint.root_of(base_url)
        self.model = model
        self.timeout = float(timeout)
        self.probe_timeout = float(probe_timeout)

    def _post(self, path: str, body: dict, timeout: float) -> dict:
        data = json.dumps(body).encode("utf-8")
        request = url_request.Request(
            self.base_url + path,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with url_request.urlopen(request, timeout=timeout) as response:
                payload = response.read()
        except url_error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            raise HybridError(f"{path} answered {exc.code}: {detail}") from exc
        except url_error.URLError as exc:
            raise HybridUnreachable(
                f"the merged service at {self.base_url} could not be reached: {exc.reason}"
            ) from exc
        except OSError as exc:
            raise HybridUnreachable(
                f"the merged service at {self.base_url} did not answer within {timeout:.0f}s: {exc}"
            ) from exc
        try:
            parsed = json.loads(payload or b"{}")
        except json.JSONDecodeError as exc:
            raise HybridError(f"{path} answered something that is not JSON: {payload[:200]!r}") from exc
        if not isinstance(parsed, dict):
            raise HybridError(f"{path} answered a {type(parsed).__name__}, not a JSON object")
        return parsed

    def complete(self, messages: list[dict]) -> dict:
        """One turn, in the OpenAI body shape the merged service answers with."""
        return self._post(
            endpoint.CHAT_PATH,
            {"model": self.model, "messages": messages, "temperature": 0, "stream": False},
            self.timeout,
        )

    def health(self) -> dict:
        request = url_request.Request(self.base_url + endpoint.HEALTH_PATH, method="GET")
        try:
            with url_request.urlopen(request, timeout=self.probe_timeout) as response:
                return json.loads(response.read() or b"{}")
        except url_error.URLError as exc:
            raise HybridUnreachable(
                f"the merged service at {self.base_url} did not answer /health: {exc.reason}"
            ) from exc

    def require_healthy(self) -> None:
        """Refuse before a run rather than score a service that is not there.

        A solver that was never reached produces empty turns, and empty turns
        score as a weak solver rather than as a missing one. The refusal is
        raised up front so that fault is never written into a run.
        """
        report = self.health()
        if report.get("status") != "ok":
            halves = report.get("halves", {})
            down = [name for name, state in halves.items() if not state.get("reachable")]
            raise HybridUnreachable(
                "the merged service is up but degraded; half(s) not reachable: "
                + (", ".join(down) if down else "unknown")
            )


# ---------------------------------------------------------------------------
# Reading a turn.
# ---------------------------------------------------------------------------


def _text_of(message: dict) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(part.get("text", "")) for part in content if isinstance(part, dict)
        )
    return ""


def _turn(body: dict) -> tuple[list[dict], str]:
    """A turn's tool calls and closing text, refused when the body has neither.

    The merged service answers a plan with calls and no text, and the end of the
    loop with text and no calls. A body carrying both would let the loop take
    the text as final and never run the plan, which is the failure the service's
    own `completion_body` exists to prevent; a body carrying neither is not an
    answer at all.
    """
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise HybridError("the turn carries no choice")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise HybridError("the turn carries no message")
    raw = message.get("tool_calls")
    calls: list[dict] = []
    if isinstance(raw, list):
        for index, call in enumerate(raw):
            if not isinstance(call, dict):
                continue
            function = call.get("function")
            function = function if isinstance(function, dict) else {}
            name = function.get("name")
            arguments = function.get("arguments", "{}")
            if not isinstance(arguments, str):
                arguments = json.dumps(arguments, separators=(",", ":"))
            calls.append(
                {
                    "id": str(call.get("id") or f"call_{index}"),
                    "name": str(name) if isinstance(name, str) else "",
                    "arguments": arguments,
                }
            )
    return calls, _text_of(message)


def _arguments(raw: str) -> tuple[dict, str]:
    """A call's arguments object, or the reason it is not one."""
    try:
        decoded = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        return {}, f"the arguments are not JSON: {exc}"
    if not isinstance(decoded, dict):
        return {}, f"the arguments are a {type(decoded).__name__} and not an object"
    return decoded, ""


# ---------------------------------------------------------------------------
# Executing a call inside the sandbox.
# ---------------------------------------------------------------------------


class Executor:
    """Applies one Hermes call to the task's workspace.

    Every method returns `(ok, observation)`. `ok` says the workspace changed or
    was read as asked; the observation is what the solver is told happened, so a
    refusal is fed back as text rather than raised, because a model that is
    stopped by an exception cannot recover and recovery is one of the things
    being measured.
    """

    def execute(self, name: str, arguments: dict, sandbox: Sandbox) -> tuple[bool, str]:
        if name not in TOOL_NAMES:
            return False, f"unknown tool {name!r}"
        try:
            handler = getattr(self, f"_tool_{name}")
        except AttributeError:
            return False, f"{name} is not available in this workspace"
        try:
            return handler(arguments, sandbox)
        except SandboxError as exc:
            return False, f"{name} refused the path: {exc}"
        except (KeyError, TypeError, ValueError, OSError) as exc:
            return False, f"{name} failed: {exc}"

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _required(arguments: dict, *names: str) -> tuple[list, str]:
        """The named arguments, or a reason and a tuple the caller can still
        unpack. The placeholder list keeps the arity fixed so a caller may write
        `(filepath,), error = self._required(...)` without a second branch."""
        missing = [name for name in names if arguments.get(name) in (None, "")]
        if missing:
            return [None] * len(names), "missing argument(s): " + ", ".join(missing)
        return [arguments[name] for name in names], ""

    # -- tools ---------------------------------------------------------------

    def _tool_read_file(self, arguments: dict, sandbox: Sandbox) -> tuple[bool, str]:
        (filepath,), error = self._required(arguments, "filepath")
        if error:
            return False, error
        if not sandbox.exists(filepath):
            return False, f"no file at {filepath}"
        return True, sandbox.read(filepath)

    def _tool_write_file(self, arguments: dict, sandbox: Sandbox) -> tuple[bool, str]:
        (filepath,), error = self._required(arguments, "filepath")
        if error:
            return False, error
        sandbox.write(filepath, str(arguments.get("content", "")))
        return True, f"wrote {filepath}"

    def _tool_append_file(self, arguments: dict, sandbox: Sandbox) -> tuple[bool, str]:
        (filepath,), error = self._required(arguments, "filepath")
        if error:
            return False, error
        sandbox.write(filepath, sandbox.read(filepath) + str(arguments.get("content", "")))
        return True, f"appended to {filepath}"

    def _tool_edit_file(self, arguments: dict, sandbox: Sandbox) -> tuple[bool, str]:
        (filepath,), error = self._required(arguments, "filepath")
        if error:
            return False, error
        if not sandbox.exists(filepath):
            return False, f"no file at {filepath}"
        old = arguments.get("old", arguments.get("find", ""))
        new = arguments.get("new", arguments.get("replace", arguments.get("content", "")))
        body = sandbox.read(filepath)
        if old and old not in body:
            return False, f"{old!r} is not in {filepath}"
        sandbox.write(filepath, body.replace(old, new) if old else str(new))
        return True, f"edited {filepath}"

    def _tool_delete_file(self, arguments: dict, sandbox: Sandbox) -> tuple[bool, str]:
        (filepath,), error = self._required(arguments, "filepath")
        if error:
            return False, error
        if not sandbox.remove(filepath):
            return False, f"no file at {filepath}"
        return True, f"deleted {filepath}"

    def _tool_move_file(self, arguments: dict, sandbox: Sandbox) -> tuple[bool, str]:
        (source, destination), error = self._required(arguments, "source", "destination")
        if error:
            return False, error
        if not sandbox.exists(source):
            return False, f"no file at {source}"
        sandbox.move(source, destination)
        return True, f"moved {source} to {destination}"

    def _tool_make_directory(self, arguments: dict, sandbox: Sandbox) -> tuple[bool, str]:
        directory = arguments.get("directory") or arguments.get("path")
        if not directory:
            return False, "missing argument(s): directory"
        sandbox.path(str(directory)).mkdir(parents=True, exist_ok=True)
        return True, f"made directory {directory}"

    def _tool_list_files(self, arguments: dict, sandbox: Sandbox) -> tuple[bool, str]:
        directory = str(arguments.get("directory") or ".")
        recursive = bool(arguments.get("recursive"))
        prefix = "" if directory in (".", "") else directory.rstrip("/") + "/"
        names = [name for name in sandbox.names() if name.startswith(prefix)]
        if not recursive:
            names = [name for name in names if "/" not in name[len(prefix):]]
        return True, ", ".join(sorted(names)) if names else "the directory is empty"

    def _tool_search_files(self, arguments: dict, sandbox: Sandbox) -> tuple[bool, str]:
        pattern = arguments.get("pattern")
        if not pattern:
            return False, "missing argument(s): pattern"
        matched = [name for name in sandbox.names() if name.endswith(str(pattern).lstrip("*"))]
        return True, ", ".join(matched) if matched else "no file matches"

    def _tool_grep_files(self, arguments: dict, sandbox: Sandbox) -> tuple[bool, str]:
        pattern = arguments.get("pattern")
        if not pattern:
            return False, "missing argument(s): pattern"
        hits: list[str] = []
        for name in sandbox.names():
            if str(pattern) in sandbox.read(name):
                hits.append(name)
        return True, ", ".join(hits) if hits else "no file contains the pattern"

    def _tool_get_file_info(self, arguments: dict, sandbox: Sandbox) -> tuple[bool, str]:
        (filepath,), error = self._required(arguments, "filepath")
        if error:
            return False, error
        if not sandbox.exists(filepath):
            return False, f"no file at {filepath}"
        return True, f"{filepath} is {len(sandbox.read(filepath))} characters"


# ---------------------------------------------------------------------------
# The loop.
# ---------------------------------------------------------------------------


def _record(
    *,
    steps: int,
    finished: bool,
    answer: str,
    had_error: bool,
    recovered: bool,
    counters: dict[str, int],
) -> dict:
    """One task's trajectory, in the shape `reference` and `runner` read."""
    body = {key: int(counters.get(key, 0)) for key in _COUNTERS}
    body["recovered"] = 1 if recovered else 0
    return {
        "steps_used": int(steps),
        "finished": bool(finished),
        "answer": answer,
        "had_error": bool(had_error),
        "recovered": bool(recovered),
        "counters": body,
    }


class Solver:
    """Drives one task to a stop and records what the model did.

    The loop is the harness and the merged service is the solver. A turn with
    calls is executed and the observations are sent back; a turn with text ends
    the loop. The budget is the task's own step budget, so a solver that never
    stops is cut off exactly where a fixture solver would be.

    The recovery flag is `had_error and finished`: a solver that met an error and
    still reached a closing turn is recorded as having come back from it, and one
    that ran out of turns still in the error is not. What the error left behind
    is judged by the task's verifier, which is the only thing that reads the end
    state, so this flag is a reading of the trajectory and not a score.
    """

    def __init__(self, client: Client, executor: Executor | None = None) -> None:
        self.client = client
        self.executor = executor or Executor()

    def solve(self, task: dict, sandbox: Sandbox) -> dict:
        budget = max(1, int(task["budget"]))
        messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": task["goal"]},
        ]

        counters = {key: 0 for key in _COUNTERS}
        seen: set[tuple[str, str]] = set()
        answer = ""
        finished = False
        had_error = False
        steps = 0

        while steps < budget:
            steps += 1
            body = self.client.complete(messages)
            calls, text = _turn(body)

            if not calls:
                # Text and no calls is the end of the loop. An empty closing turn
                # is still a stop: the model chose to say nothing, and reading
                # that as "keep going" would spend the budget on a model that had
                # already finished.
                answer = text
                finished = True
                break

            assistant_calls: list[dict] = []
            turn_failed = True
            for call in calls:
                counters["tool_calls"] += 1
                name = call["name"]
                arguments, error = _arguments(call["arguments"])

                if not name:
                    counters["unstructured_tool_calls"] += 1
                    counters["invalid_actions"] += 1
                    ok, observation = False, "the call named no tool"
                elif name not in TOOL_NAMES:
                    counters["unknown_tools"] += 1
                    counters["invalid_actions"] += 1
                    ok, observation = False, f"unknown tool {name!r}"
                elif error:
                    counters["invalid_args"] += 1
                    counters["invalid_actions"] += 1
                    ok, observation = False, error
                else:
                    if (name, call["arguments"]) in seen:
                        counters["redundant_calls"] += 1
                    ok, observation = self.executor.execute(name, arguments, sandbox)

                if ok:
                    counters["successful_tool_calls"] += 1
                    turn_failed = False
                else:
                    # A known tool that ran and failed is a tool error; a call
                    # that never reached the executor is not, because it was
                    # already counted as the kind of invalid action it was.
                    if name and name in TOOL_NAMES and not error:
                        counters["tool_errors"] += 1
                    had_error = True

                seen.add((name, call["arguments"]))
                assistant_calls.append(
                    {"id": call["id"], "type": "function", "function": {"name": name, "arguments": call["arguments"]}}
                )
                messages.append(
                    {"role": "tool", "tool_call_id": call["id"], "name": name, "content": observation}
                )

            # The assistant turn is inserted ahead of its observations so the
            # service's digest reads the calls and then what they returned, in
            # the order a harness records them.
            messages.insert(
                len(messages) - len(calls),
                {"role": "assistant", "content": "", "tool_calls": assistant_calls},
            )

            if turn_failed:
                counters["repeated_failed_turns"] += 1

        # A loop that left through the budget guard never saw a closing turn, so
        # `finished` is already false and the record says the solver was stopped
        # rather than that it stopped.
        return _record(
            steps=steps,
            finished=finished,
            answer=answer,
            had_error=had_error,
            recovered=bool(had_error and finished),
            counters=counters,
        )


# ---------------------------------------------------------------------------
# The profile.
# ---------------------------------------------------------------------------


def behaviours(client: Client) -> dict[str, callable]:
    """A behaviour per task id, in the shape `reference.PROFILES` holds.

    Each entry is a callable that takes the task's sandbox, exactly as a fixture
    does, so `runner.run` needs no branch for a live solver. The goal and the
    budget come from the suite's own task definition, which is the point: the
    live solver is measured against the same nine tasks and the same arithmetic
    as the two fixtures.
    """
    solver = Solver(client)
    table: dict[str, callable] = {}

    module = _suite_module()
    for task in module.suite(module.SUITE_ALL):
        table[task["id"]] = _behaviour(solver, task)
    return table


def _behaviour(solver: Solver, task: dict):
    task_id = task["id"]
    goal = task["goal"]
    budget = int(task["budget"])

    def act(sandbox: Sandbox) -> dict:
        # The task object handed to the runner is rebuilt per call, so the id,
        # goal and budget are bound here and the sandbox is the only input. This
        # is what makes the entry interchangeable with a fixture.
        return solver.solve({"id": task_id, "goal": goal, "budget": budget}, sandbox)

    return act


def _suite_module():
    try:
        from . import suite  # type: ignore
    except ImportError:
        import suite  # type: ignore
    return suite


def register(
    name: str = "q2-hybrid",
    *,
    url: str = "http://127.0.0.1:8730",
    model: str = "q2-planner-coder",
    timeout: float = 300.0,
) -> Client:
    """Register the merged service as a solver profile and return its client.

    Registering is the whole side effect. Nothing is sent until a run asks for a
    behaviour, so a caller may register and then decide not to run, and a run
    that never reaches the service leaves no run behind because `runner` fails
    before writing one.
    """
    client = Client(url, model=model, timeout=timeout)
    reference.register_profile(name, behaviours(client))
    return client
