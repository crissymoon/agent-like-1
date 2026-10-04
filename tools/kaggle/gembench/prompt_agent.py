"""A live solver that drives a model through the harness prompt protocol.

`hybrid.py` measures a service that answers with structured `tool_calls`, which
is how a merged planner-coder is served. The post-trained model is a different
thing: it answers with one JSON object of its own, read and carried out by the
runtime in `lib/AgentLoop.php`. A benchmark that could only see the structured
shape would have to pretend the model speaks a protocol it was never trained on,
so this module is the second live profile: the same nine tasks, the same
arithmetic, the same writer, driven through the protocol the harness enforces.

Three details of that protocol are reproduced rather than approximated, because
each one changes what a solver sees and therefore what the run measures.

  The system prompt is the text of `AgentPrompt::prompted()`, rendered from the
  tool table in `lib/ToolRegistry.php`. The menu lines, their order, the rule
  numbering and the step count are the harness's own, so the condition is the
  one a deployed run is taken under rather than a paraphrase of it.
  `test_prompt_agent.py` reads the PHP back and compares, so a tool or a rule
  edited on the harness side fails there instead of quietly measuring something
  else.

  An observation comes back as a `user` turn prefixed `Observation: `, which is
  what `AgentLoop::observationMessage()` writes in prompt mode. Feeding it back
  as a `tool` turn would be the native shape, and this model was not trained on
  it.

  A repeated call is refused once it has failed. `AgentLoop` will not send the
  same call twice after a failure, so a loop here that resent it would measure a
  runtime nobody runs; the refusal is recorded as a repeated failed turn.

The one place this module does more than read is `run_command`. The registry
offers a shell and the suite has a task that needs one: the correct sequence for
`organize_directory` is a single `mv`, and there is no move tool in the
registry. The portable workspace has files and no shell, so `run_command` is
carried out by `prompt_shell.ShellBridge`, which implements exactly the twelve
words `SandboxPolicy::summary()` names to the model and refuses the rest by
name. The number of bridge calls made is reported with every run, so a reader
can see how much of a score rests on it rather than having to trust that none of
it does.
"""

from __future__ import annotations

import fnmatch
import json
from urllib import error as url_error
from urllib import request as url_request

try:  # the package on a machine
    from . import endpoint
    from . import grammar as grammar_module
    from .prompt_shell import DOCUMENTED_COMMANDS, ShellBridge
    from .sandbox import Sandbox, SandboxError
except ImportError:  # a notebook cell or a direct script
    import endpoint  # type: ignore
    import grammar as grammar_module  # type: ignore
    from prompt_shell import DOCUMENTED_COMMANDS, ShellBridge  # type: ignore
    from sandbox import Sandbox, SandboxError  # type: ignore


class PromptAgentError(RuntimeError):
    """The model's service cannot answer, or answered something unusable."""


class PromptUnreachable(PromptAgentError):
    """The service was never reached, which is not a solver failure."""


# ---------------------------------------------------------------------------
# The tool surface, transcribed from lib/ToolRegistry.php
# ---------------------------------------------------------------------------

INCUBATOR_SUMMARY = (
    "Run the sql-mgr incubator command: query and grow the vector module registry, "
    "load it from any SQL database, or replicate shards between nodes. Give the verb "
    'and its options, for example "dialects", "route --domain code --id alpha" or '
    '"search --domain code --text router --k 3". The command may read any SQL database '
    "it is pointed at; the registry it writes stays inside the workspace. The sql-mgr "
    "engine is developed separately in a private repository and has no public release "
    "at the moment; this tool is the only interface to it."
)

#: Name, summary and required arguments of every tool, in registry order.
TOOL_SPECS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("list_files", "List every file in the workspace, or in one directory of it.", ("path",)),
    ("read_file", "Read one text file.", ("path",)),
    ("write_file", "Create or overwrite a file with the given content.", ("path", "content")),
    ("append_file", "Add content to the end of an existing file, creating it if absent.", ("path", "content")),
    ("delete_file", "Delete one file or directory from the workspace.", ("path",)),
    ("make_directory", "Create a directory, including any missing parents.", ("path",)),
    ("search_files", 'Find files whose path matches a glob such as "*.tmp".', ("pattern",)),
    (
        "run_command",
        'Run one shell pipeline inside the workspace, for example "wc -l data/x.log" '
        'or "grep -c error app.log". Allowed tools: ' + ", ".join(DOCUMENTED_COMMANDS),
        ("command",),
    ),
    ("sql_incubator", INCUBATOR_SUMMARY, ("args",)),
    ("finish", "End the task and report the final answer.", ("answer",)),
)

TOOL_NAMES = frozenset(name for name, _, _ in TOOL_SPECS)
TOOL_REQUIRED = {name: required for name, _, required in TOOL_SPECS}

#: The tools a portable workspace cannot carry out at all. `run_command` is not
#: here: the bridge carries it out over the subset the policy names.
UNAVAILABLE_TOOLS = frozenset({"sql_incubator"})

#: The counters a solver record carries, mirroring `reference._DEFAULT_COUNTERS`.
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

#: The maximum length of an observation fed back to the solver, which is the
#: harness's own `HARNESS_AGENT_MAX_OBSERVATION`.
DEFAULT_MAX_OBSERVATION = 3000

#: The cap on one turn's reply, which is the harness's `HARNESS_AGENT_MAX_TOKENS`.
#: It is stated as the harness states it because the number decides what happens
#: to a model that writes a preamble before its object: too low and the object is
#: cut off, which reads as a protocol failure and is really a setting.
DEFAULT_MAX_TOKENS = 768

#: The sampling condition a recorded run was taken under, stated as the harness
#: states it. Greedy with a fixed seed is the harness's own setting, so it is the
#: default here and no recorded run changes meaning by these existing. Above zero
#: is a named condition a sweep may cross, and a run that used one has to say so:
#: the reply carries no record of how it was sampled.
DEFAULT_TEMPERATURE = 0.0
DEFAULT_SEED = 0

#: The decoder conditions a run can be taken under, named as the harness names
#: them in `HARNESS_AGENT_DECODER`. `none` is the harness's own default, so it is
#: the condition a recorded run was taken under. The other two are both grammars
#: and they differ in one escaped pair. `grammar` is what `ActionSchema::gbnf()`
#: renders now: the tool name inside JSON quotes. `grammar-bare` is the rendering
#: the harness shipped before that was repaired, which emits the name as a GBNF
#: literal and therefore cannot produce an object `json_decode` accepts. Measuring
#: both is what separates a failure of the model from a failure of the sampler:
#: under `grammar-bare` every one of five quants scored 0 of 9 tasks, and under
#: `none` the same weights scored 5 to 8 of 9.
DECODER_NONE = "none"
DECODER_GRAMMAR = "grammar"
DECODER_GRAMMAR_BARE = "grammar-bare"
DECODERS = (DECODER_NONE, DECODER_GRAMMAR, DECODER_GRAMMAR_BARE)


def sampling_tag(temperature: float, seed: int) -> str:
    """One sampling condition as a name a directory or a table column can carry.

    Written once, here, because the sweep's driver, the runner's record and the
    reader that joins two runs have to agree on it. `0:0` is greedy and is spelled
    `t0s0`; a leading zero on a decimal is dropped so that `0.7` and `.7` are one
    name rather than two rows of the same condition.
    """
    number = f"{float(temperature):g}"
    return f"t{number}s{int(seed)}"


def action_grammar(decoder: str = DECODER_GRAMMAR) -> str:
    """The GBNF a grammar decoder sends, generated from the tool table."""
    names = grammar_module.action_names([name for name, _, _ in TOOL_SPECS])
    return grammar_module.gbnf(names, quoted=decoder != DECODER_GRAMMAR_BARE)


def prompt_menu() -> str:
    """The tool list as `ToolRegistry::promptMenu()` renders it.

    A parameter the registry marks optional renders with a question mark. None
    of the ten currently has one, so the marker never appears; the branch is
    here because the harness has one and a menu that could not express it would
    drift the moment a tool gained an optional argument.
    """
    lines = []
    for name, summary, required in TOOL_SPECS:
        arguments = ", ".join(f'"{argument}"' for argument in required)
        lines.append(f"- {name}: {{{arguments}}} - {summary}")
    return "\n".join(lines)


def system_prompt(step_budget: int) -> str:
    """The system turn, as `AgentPrompt::prompted()` writes it for *step_budget*.

    The budget is rendered rather than fixed because rule 5 tells the solver how
    many turns it has and the loop enforces the task's own budget. A prompt that
    promised eight turns under a five turn cap would describe a condition no
    deployed run is taken under.
    """
    return (
        "You are an autonomous agent working inside a small sandboxed workspace.\n"
        "\n"
        "Reply with exactly one JSON object on every turn. Write no other text, and do not use code fences.\n"
        "\n"
        "To use a tool, reply with:\n"
        '{"action": "tool", "tool": "<tool>", "args": {"<argument>": "<value>"}}\n'
        "\n"
        "To end the task, reply with:\n"
        '{"action": "finish", "answer": "<short final answer>"}\n'
        "\n"
        "Tools:\n"
        f"{prompt_menu()}\n"
        "\n"
        "Rules:\n"
        "1. Exactly one JSON object per turn, and nothing else on the turn.\n"
        "2. Every path is relative to the workspace root. Never use an absolute path.\n"
        "3. Look at the workspace with a tool before you assume anything about it.\n"
        "4. When a tool replies with ERROR, read the message and try a different approach rather than repeating the same call.\n"
        f"5. You have at most {step_budget} turns. Use the fewest calls that finish the task.\n"
        "6. Change only the files the task asks you to change.\n"
        "7. Call finish once the task is done."
    )


def observation_prompt(observation: str) -> str:
    """The turn an observation is fed back as, which is `AgentLoop`'s own shape."""
    return "Observation: " + observation


# ---------------------------------------------------------------------------
# The transport
# ---------------------------------------------------------------------------


class Client:
    """One OpenAI-compatible address in front of the model.

    The body is shaped here and nothing about it is interpreted: every decision
    about what a turn means belongs to the reader and the loop, so this class
    has one job and a stub can stand in for it in a test.
    """

    def __init__(
        self,
        base_url: str,
        model: str = "",
        timeout: float = 300.0,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        decoder: str = DECODER_NONE,
        temperature: float = DEFAULT_TEMPERATURE,
        seed: int = DEFAULT_SEED,
    ) -> None:
        if decoder not in DECODERS:
            raise ValueError(f"unknown decoder {decoder!r} (known: {', '.join(DECODERS)})")
        if float(temperature) < 0:
            raise ValueError(f"temperature must not be negative: {temperature!r}")
        #: The service's root, from whichever spelling of its url was handed in.
        #: The documented command line passes the completions endpoint and the
        #: argument is documented as a base url, so the two are reconciled once,
        #: here, rather than at each call site: appending the chat path to an
        #: address that already carries it builds a 404, which a run reads as a
        #: model that is missing.
        self.base_url = endpoint.root_of(base_url)
        self.model = model
        self.timeout = float(timeout)
        self.max_tokens = int(max_tokens)
        self.decoder = decoder
        #: The sampling condition. Greedy decoding with a fixed seed is the
        #: harness's own condition and the default, so a recorded run is unchanged
        #: by this field being here: two runs of it are byte-identical, which is
        #: what makes a repeat a control rather than a second sample.
        #:
        #: Above zero the same weights produce different trajectories, and that is
        #: the only lever that makes a repeat informative. A corpus of
        #: preference pairs needs instances where a run diverges from its own
        #: better half, and a greedy run that loops on one refused command for
        #: thirty-six turns is one instance however many times it is repeated.
        #:
        #: Both numbers are recorded with every run and in its manifest, because a
        #: score taken at one sampling condition is not comparable with a score
        #: taken at another and nothing in the reply itself says which it was.
        self.temperature = float(temperature)
        self.seed = int(seed)
        #: The grammar sent to the engine under either grammar decoder. It is
        #: built once, so a run's manifest can carry its digest and two runs that
        #: claim the same condition can be shown to have been that condition.
        #: Both grammar conditions send one: a condition whose name says grammar
        #: and whose body carries no grammar is a run that measured `none` while
        #: its record said otherwise.
        self.grammar = "" if decoder == DECODER_NONE else action_grammar(decoder)
        #: Filled in by `behaviours`, so a caller can read the turns back.
        self.solver = None
        #: Turns sent, and the token counts the server reported for them, so a
        #: run can say what it cost and not only what it scored.
        self.turns = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0

    def complete(self, messages: list[dict]) -> str:
        """One turn's reply text."""
        body: dict = {
            "messages": messages,
            "temperature": self.temperature,
            "seed": self.seed,
            "max_tokens": self.max_tokens,
            "stream": False,
        }
        if self.model:
            body["model"] = self.model
        if self.grammar:
            # The field name is llama.cpp's. `DecodingConstraint` makes the same
            # point from the other side: a grammar sent to an endpoint that does
            # not read it is ignored rather than an error, so a run that used one
            # has to record that it did.
            body["grammar"] = self.grammar
        request = url_request.Request(
            self.base_url + endpoint.CHAT_PATH,
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with url_request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read() or b"{}")
        except url_error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            raise PromptAgentError(f"the model answered {exc.code}: {detail}") from exc
        except url_error.URLError as exc:
            raise PromptUnreachable(
                f"the model at {self.base_url} could not be reached: {exc.reason}"
            ) from exc
        except OSError as exc:
            raise PromptUnreachable(
                f"the model at {self.base_url} did not answer within {self.timeout:.0f}s: {exc}"
            ) from exc

        self.turns += 1
        usage = payload.get("usage") or {}
        self.prompt_tokens += int(usage.get("prompt_tokens") or 0)
        self.completion_tokens += int(usage.get("completion_tokens") or 0)

        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise PromptAgentError("the reply carries no choice")
        message = choices[0].get("message") or {}
        text = message.get("content")
        if not isinstance(text, str) or not text.strip():
            # A reply whose text arrived in the reasoning field and left
            # `content` empty is still a reply, and reading it as empty would
            # score a server-side formatting difference as a lost turn.
            reasoning = message.get("reasoning_content")
            if isinstance(reasoning, str):
                text = reasoning
        return text if isinstance(text, str) else ""

    def health(self) -> bool:
        try:
            with url_request.urlopen(self.base_url + endpoint.HEALTH_PATH, timeout=5) as response:
                return response.status == 200
        except (url_error.URLError, OSError):
            return False


# ---------------------------------------------------------------------------
# Reading a turn
# ---------------------------------------------------------------------------


def _balanced_objects(text: str) -> list[str]:
    """Every balanced brace-delimited span in *text*, in order.

    A brace inside a string is not a brace, so the scan tracks the string state
    and the escape state. A model that wraps its object in a code fence, or
    writes a sentence and then the object, is read the same as one that wrote
    the object alone, which is what `AgentAction` does with its span recovery
    and for the same reason.
    """
    spans: list[str] = []
    depth = 0
    start = 0
    in_string = False
    escaped = False
    for index, character in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character == "{":
            if depth == 0:
                start = index
            depth += 1
        elif character == "}" and depth:
            depth -= 1
            if depth == 0:
                spans.append(text[start : index + 1])
    return spans


def _strip_fences(text: str) -> str:
    """A reply with its code fence markers removed, when it has any."""
    body = text.strip()
    if not body.startswith("```"):
        return body
    lines = body.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


_TOOL_KEYS = ("tool", "tool_name", "name", "action", "function")
_ARG_KEYS = ("args", "arguments", "parameters", "input")
_ANSWER_KEYS = ("answer", "final_answer", "result", "response", "output", "summary")

_INVALID = {"kind": "invalid", "tool": "", "args": {}, "answer": "", "error": "", "shape": ""}


def _argument_object(decoded: dict) -> dict:
    """The call's arguments, from a container or from the sibling keys."""
    for key in _ARG_KEYS:
        value = decoded.get(key)
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                nested = json.loads(value)
            except json.JSONDecodeError:
                nested = None
            if isinstance(nested, dict):
                return nested
    return {
        key: value
        for key, value in decoded.items()
        if key not in _TOOL_KEYS and key not in _ARG_KEYS and key not in _ANSWER_KEYS
    }


def read_turn(text: str) -> dict:
    """One reply read as one intended action.

    Returns a dict whose `kind` is `tool`, `finish` or `invalid`. A reply that
    parses but names no tool and carries no answer is invalid rather than
    invented into a call, because the counters are how a run says a model
    stopped speaking the protocol.
    """
    if not isinstance(text, str):
        return {**_INVALID, "error": "the turn is not text"}

    candidates: list[str] = []
    for source in (text.strip(), _strip_fences(text)):
        if source and source not in candidates:
            candidates.append(source)
    for source in list(candidates):
        candidates.extend(span for span in _balanced_objects(source) if span not in candidates)

    for candidate in candidates:
        try:
            decoded = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(decoded, dict):
            continue

        tool = ""
        tool_key = ""
        for key in _TOOL_KEYS:
            value = decoded.get(key)
            if isinstance(value, str) and value.strip():
                tool = value.strip()
                tool_key = key
                break

        answer = ""
        for key in _ANSWER_KEYS:
            value = decoded.get(key)
            if isinstance(value, str):
                answer = value
                break
            if isinstance(value, (int, float, bool)):
                answer = str(value)
                break

        if not tool or tool.lower() in ("finish", "done"):
            if tool or answer or any(key in decoded for key in _ANSWER_KEYS):
                return {
                    "kind": "finish",
                    "tool": "finish",
                    "args": {},
                    "answer": answer,
                    "error": "",
                    "shape": "canonical" if tool_key else "alias",
                }
            return {**_INVALID, "error": "the object names neither a tool nor an answer"}

        arguments = _argument_object(decoded)
        canonical = tool_key in ("tool", "action") and any(key in decoded for key in _ARG_KEYS)
        return {
            "kind": "tool",
            "tool": tool,
            "args": arguments,
            "answer": "",
            "error": "",
            "shape": "canonical" if canonical else "alias",
        }

    return {**_INVALID, "error": "no JSON object in the reply"}


# ---------------------------------------------------------------------------
# Executing a call inside the workspace
# ---------------------------------------------------------------------------


class Executor:
    """Applies one harness action to the task's workspace.

    Every method returns `(ok, observation)`. A refusal is fed back as text
    rather than raised, because a model stopped by an exception cannot recover
    and recovery is one of the things measured. An observation is worded as
    `ToolRegistry` words it, so what a solver reads here is what it would read
    from the harness.
    """

    def __init__(self, max_observation: int = DEFAULT_MAX_OBSERVATION) -> None:
        self.max_observation = int(max_observation)
        self.bridge = ShellBridge()
        #: How many calls were carried out through the shell bridge, which is
        #: reported with the run so its reach is visible rather than assumed.
        self.bridge_calls = 0

    def execute(self, tool: str, arguments: dict, sandbox: Sandbox) -> tuple[bool, str]:
        if tool not in TOOL_NAMES:
            return False, self._error(
                f'there is no tool named "{tool}". Available tools: ' + ", ".join(TOOL_NAMES)
            )
        if tool in UNAVAILABLE_TOOLS:
            return False, self._error(f"{tool} is not reachable from this workspace")
        if not isinstance(arguments, dict):
            return False, self._error(f"{tool} needs an argument object")

        missing = [name for name in TOOL_REQUIRED[tool] if name not in arguments]
        if missing:
            return False, self._error(f"{tool} needs the argument(s) " + ", ".join(missing))

        try:
            return getattr(self, "_tool_" + tool)(arguments, sandbox)
        except SandboxError as problem:
            return False, self._error(f"{tool} refused the path: {problem}")
        except (KeyError, TypeError, ValueError, OSError) as problem:
            return False, self._error(f"{tool} failed: {problem}")

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _error(message: str) -> str:
        return "ERROR: " + message

    @staticmethod
    def _text(arguments: dict, key: str, allow_empty: bool = False) -> str:
        """An argument as a string.

        A model sometimes nests an object or a list where a string belongs.
        Flattening is friendlier than failing, and is what `ToolRegistry` does
        with the same shape.
        """
        value = arguments.get(key)
        if isinstance(value, (dict, list)):
            value = json.dumps(value, separators=(",", ":"))
        elif isinstance(value, bool):
            value = "true" if value else "false"
        elif isinstance(value, (int, float)):
            value = str(value)
        if not isinstance(value, str):
            raise ValueError(f"{key} must be a string")
        if not allow_empty and not value.strip():
            raise ValueError(f"{key} must not be empty")
        return value

    def _clip(self, text: str, label: str) -> str:
        if len(text) <= self.max_observation:
            return text
        return (
            text[: self.max_observation]
            + f"\n... ({self.max_observation} of {len(text)} bytes shown for {label})"
        )

    # -- tools ---------------------------------------------------------------

    def _tool_list_files(self, arguments: dict, sandbox: Sandbox) -> tuple[bool, str]:
        prefix = self._text(arguments, "path").strip("/")
        prefix = "" if prefix == "." else prefix + "/"
        rows = [
            f"{entry['path']} ({entry['bytes']} bytes)"
            for entry in sandbox.inventory()
            if not entry["is_dir"] and (not prefix or entry["path"].startswith(prefix))
        ]
        if not rows:
            return True, "the workspace is empty"
        return True, f"{len(rows)} file(s):\n" + "\n".join(rows)

    def _tool_read_file(self, arguments: dict, sandbox: Sandbox) -> tuple[bool, str]:
        path = self._text(arguments, "path")
        if not sandbox.exists(path):
            return False, self._error(f"no file at {path}")
        return True, self._clip(sandbox.read(path), path)

    def _tool_write_file(self, arguments: dict, sandbox: Sandbox) -> tuple[bool, str]:
        path = self._text(arguments, "path")
        content = self._text(arguments, "content", allow_empty=True)
        sandbox.write(path, content)
        return True, f"wrote {len(content.encode('utf-8'))} bytes to {path}"

    def _tool_append_file(self, arguments: dict, sandbox: Sandbox) -> tuple[bool, str]:
        path = self._text(arguments, "path")
        content = self._text(arguments, "content", allow_empty=True)
        sandbox.write(path, sandbox.read(path) + content)
        return True, f"appended {len(content.encode('utf-8'))} bytes to {path}"

    def _tool_delete_file(self, arguments: dict, sandbox: Sandbox) -> tuple[bool, str]:
        path = self._text(arguments, "path")
        if not sandbox.remove(path):
            return False, self._error(f"no file at {path}")
        return True, f"deleted {path}"

    def _tool_make_directory(self, arguments: dict, sandbox: Sandbox) -> tuple[bool, str]:
        path = self._text(arguments, "path")
        sandbox.path(path).mkdir(parents=True, exist_ok=True)
        return True, f"created directory {path}"

    def _tool_search_files(self, arguments: dict, sandbox: Sandbox) -> tuple[bool, str]:
        pattern = self._text(arguments, "pattern")
        matches = [name for name in sandbox.names() if fnmatch.fnmatch(name, pattern)]
        if not matches:
            return True, f'no file matches "{pattern}"'
        return True, f'{len(matches)} match(es) for "{pattern}":\n' + "\n".join(matches)

    def _tool_run_command(self, arguments: dict, sandbox: Sandbox) -> tuple[bool, str]:
        command = self._text(arguments, "command")
        self.bridge_calls += 1
        ok, observation = self.bridge.run(command, sandbox)
        return ok, self._clip(observation, command)

    def _tool_finish(self, arguments: dict, sandbox: Sandbox) -> tuple[bool, str]:
        return True, "task finished"


# ---------------------------------------------------------------------------
# The loop
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


def _complaint(name: str, arguments: object) -> str:
    """Why a call cannot be carried out, or the empty string when it can."""
    if not isinstance(arguments, dict):
        return "the args are not an object"
    missing = [key for key in TOOL_REQUIRED.get(name, ()) if key not in arguments]
    if missing:
        return f"{name} needs the argument(s) " + ", ".join(missing)
    return ""


class Solver:
    """Drives one task to a stop and records what the model did.

    The model is the solver and this is the runtime: a turn naming a tool is
    carried out and fed back, a turn ending the task stops the loop, and a turn
    that is neither is refused and counted. The budget is the task's own, so a
    solver that never stops is cut off where a fixture solver would be.
    """

    def __init__(self, client: Client, executor: Executor | None = None) -> None:
        self.client = client
        self.executor = executor or Executor()
        #: Every turn of every task, keyed by task id. The runner writes a
        #: trajectory's counters and its end state and nothing about how they
        #: were reached, and a counter says a turn was unstructured without
        #: saying what was written. This is what a reader needs to see the
        #: difference between a model that planned wrongly and one that could
        #: not say what it meant.
        self.transcripts: dict[str, list[dict]] = {}

    def _note(self, task_id: str, step: int, reply: str, observation: str, kind: str, **fields) -> None:
        entry = {"step": int(step), "kind": kind, "reply": reply, "observation": observation}
        entry.update(fields)
        self.transcripts.setdefault(task_id, []).append(entry)

    def solve(self, task: dict, sandbox: Sandbox) -> dict:
        budget = max(1, int(task["budget"]))
        messages: list[dict] = [
            {"role": "system", "content": system_prompt(budget)},
            {"role": "user", "content": task["goal"]},
        ]

        counters = {key: 0 for key in _COUNTERS}
        failed: set[str] = set()
        seen: set[str] = set()
        answer = ""
        finished = False
        had_error = False
        steps = 0

        while steps < budget:
            steps += 1
            text = self.client.complete(messages)
            turn = read_turn(text)
            messages.append({"role": "assistant", "content": text})

            if turn["kind"] == "finish":
                answer = turn["answer"]
                finished = True
                self._note(task["id"], steps, text, "", "finish")
                break

            if turn["kind"] == "invalid":
                counters["invalid_actions"] += 1
                counters["unstructured_tool_calls"] += 1
                counters["repeated_failed_turns"] += 1
                had_error = True
                complaint = (
                    "the turn is not one JSON object. "
                    + (turn["error"] or "reply with the documented shape")
                )
                self._note(task["id"], steps, text, complaint, "invalid")
                messages.append(
                    {
                        "role": "user",
                        "content": observation_prompt(self.executor._error(complaint)),
                    }
                )
                continue

            name = turn["tool"]
            arguments = turn["args"]
            counters["tool_calls"] += 1
            signature = name + "|" + json.dumps(arguments, sort_keys=True, separators=(",", ":"))

            complaint = ""
            if name not in TOOL_NAMES:
                counters["unknown_tools"] += 1
                complaint = f'unknown tool "{name}"'
            elif name in UNAVAILABLE_TOOLS:
                complaint = f"{name} is not reachable from this workspace"
            else:
                complaint = _complaint(name, arguments)

            if complaint:
                counters["invalid_actions"] += 1
                if name in TOOL_NAMES and "needs the argument(s)" in complaint:
                    counters["invalid_args"] += 1
                ok, observation = False, self.executor._error(complaint)
            elif signature in failed:
                # The runtime refuses a call it has already sent and seen fail,
                # rather than spending a turn on the same mistake twice.
                counters["redundant_calls"] += 1
                counters["repeated_failed_turns"] += 1
                ok, observation = False, self.executor._error(
                    "that call has already failed; read the error and try a different approach"
                )
            else:
                if signature in seen:
                    counters["redundant_calls"] += 1
                ok, observation = self.executor.execute(name, arguments, sandbox)
                seen.add(signature)

            if ok:
                counters["successful_tool_calls"] += 1
            else:
                had_error = True
                if not complaint:
                    # A known tool with usable arguments that the workspace
                    # refused is a tool error. A call that never reached the
                    # executor is not, because it was already counted as the
                    # kind of invalid action it was.
                    counters["tool_errors"] += 1
                failed.add(signature)

            self._note(task["id"], steps, text, observation, "tool", tool=name, ok=ok, shape=turn["shape"])
            messages.append({"role": "user", "content": observation_prompt(observation)})

        return _record(
            steps=steps,
            finished=finished,
            answer=answer,
            had_error=had_error,
            recovered=bool(had_error and finished),
            counters=counters,
        )


# ---------------------------------------------------------------------------
# The profile
# ---------------------------------------------------------------------------


def behaviours(client: Client, executor: Executor | None = None) -> dict[str, callable]:
    """A behaviour per task id, in the shape `reference.PROFILES` holds."""
    solver = Solver(client, executor)
    # The caller reaches the turn-by-turn record through the client it already
    # holds, rather than through a second return value that every existing
    # caller of `register` would have to be changed to unpack.
    client.solver = solver
    module = _suite_module()
    return {task["id"]: _behaviour(solver, task) for task in module.suite(module.SUITE_ALL)}


def _behaviour(solver: Solver, task: dict):
    task_id = task["id"]
    goal = task["goal"]
    budget = int(task["budget"])

    def act(sandbox: Sandbox) -> dict:
        # The task object handed to the runner is rebuilt per call, so the id,
        # goal and budget are bound here and the sandbox is the only input.
        # That is what makes this entry interchangeable with a fixture.
        return solver.solve({"id": task_id, "goal": goal, "budget": budget}, sandbox)

    return act


def _suite_module():
    try:
        from . import suite  # type: ignore
    except ImportError:
        import suite  # type: ignore
    return suite


def _reference_module():
    try:
        from . import reference  # type: ignore
    except ImportError:
        import reference  # type: ignore
    return reference


def register(
    name: str = "gemma-prompt",
    *,
    url: str = "http://127.0.0.1:8793",
    model: str = "",
    timeout: float = 300.0,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    max_observation: int = DEFAULT_MAX_OBSERVATION,
    decoder: str = DECODER_NONE,
    temperature: float = DEFAULT_TEMPERATURE,
    seed: int = DEFAULT_SEED,
) -> Client:
    """Register a prompt-protocol service as a solver profile and return it."""
    client = Client(
        url,
        model=model,
        timeout=timeout,
        max_tokens=max_tokens,
        decoder=decoder,
        temperature=temperature,
        seed=seed,
    )
    executor = Executor(max_observation)
    _reference_module().register_profile(name, behaviours(client, executor))
    return client
