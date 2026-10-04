"""Check the prompt-protocol solver against the harness and the suite.

Five facts are asserted, and each is a way the measurement could be wrong:

  1. The tool table in `prompt_agent` is the table `lib/ToolRegistry.php`
     declares, read back out of the PHP rather than trusted. A tool, a summary
     or a required argument edited on the harness side fails here.

  2. The system prompt is the text `AgentPrompt::prompted()` renders, checked by
     rebuilding the menu from the PHP and comparing whole prompts. A solver that
     measured a paraphrased prompt would not be measuring the deployed one.

  3. A reply is read the way `AgentAction` reads one: the documented object, a
     fenced object, an object behind prose and a flattened call all resolve, and
     prose with no object is refused rather than invented into a call.

  4. The shell bridge carries out the twelve documented words and refuses
     everything else, so a model cannot reach anything the prompt never offered.

  5. A solver that emits the correct sequence for each task passes all nine
     verifiers. This is the fact the rest rests on: it proves the executor can
     reach every end state the suite judges, so a later failure is the model's
     and not the harness's.

Run it after any change to the solver or the harness:
`python3 tools/kaggle/gembench/test_prompt_agent.py`.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if __package__ in (None, ""):
    sys.path.insert(0, str(HERE.parent))

from gembench import grammar  # noqa: E402
from gembench import prompt_agent as agent  # noqa: E402
from gembench import prompt_shell, runner  # noqa: E402
from gembench import suite as suite_module  # noqa: E402
from gembench.sandbox import Sandbox  # noqa: E402


def harness_root() -> Path:
    """The harness checkout, wherever it sits relative to this repository."""
    for candidate in (HERE.parent.parent.parent, HERE.parent.parent, HERE.parent):
        if (candidate / "lib" / "ToolRegistry.php").is_file():
            return candidate
    return HERE.parent.parent.parent


HARNESS = harness_root()
REGISTRY_PHP = HARNESS / "lib" / "ToolRegistry.php"
PROMPT_PHP = HARNESS / "lib" / "AgentPrompt.php"
SELF_CHECK_PHP = HARNESS / "lib" / "AgentSelfCheck.php"


def endpoint_cases() -> list[tuple[str, str, str]]:
    """The service-url cases, read out of the harness's own self-check table.

    That table is the one thing both sides are pinned to: `EngineProfile::rootOf()`
    is checked against it by the harness self-check, and the two live profiles
    here are checked against it below. Reading it rather than restating it means
    a case added on the harness side is a case this module has to answer, and a
    case edited there is an edit here, so the two cannot drift into disagreeing
    about the same url.
    """
    try:
        text = SELF_CHECK_PHP.read_text(encoding="utf-8")
    except OSError:
        return []
    start = text.find("private function endpointRootChecks")
    if start < 0:
        return []
    body = text[start:]
    end = body.find("foreach ($cases as")
    if end < 0:
        return []
    return [
        (name, given, expected)
        for name, given, expected in re.findall(
            r"'([^']*)'\s*=>\s*\[\s*'([^']*)',\s*'([^']*)',\s*\],", body[:end]
        )
    ]


def check_endpoint(failures: list[str]) -> None:
    """The two spellings of a service url must name one address.

    The defect this pins reads as a missing model. The documented command line
    passes the completions endpoint, the argument is documented as a base url,
    and appending the chat path to the endpoint builds
    `.../v1/chat/completions/v1/chat/completions`, which the server answers 404.
    The harness repaired its side; the two live profiles are required to answer
    the harness's own cases, and the property that was false is asserted
    directly so a future edit that reintroduces it fails here.
    """
    try:
        from gembench import endpoint, hybrid
    except ImportError as problem:
        failures.append(f"the endpoint rule could not be imported: {problem}")
        return

    cases = endpoint_cases()
    if len(cases) < 5:
        failures.append(
            f"the harness service-url table yielded {len(cases)} cases, so this check "
            "would pass on an empty read"
        )
    for name, given, expected in cases:
        got = endpoint.root_of(given)
        if got != expected:
            failures.append(f"{name}: {given} -> {got}, the harness says {expected}")
        chat = endpoint.chat_url(given)
        if chat.count(endpoint.CHAT_PATH) != 1:
            failures.append(f"{name}: the chat url carries its path {chat.count(endpoint.CHAT_PATH)} times: {chat}")
        probe = endpoint.health_url(given)
        if re.search(r"/v[0-9]+[a-z]*/", probe):
            failures.append(f"{name}: the readiness probe is built under a versioned path: {probe}")

    # The clients and not only the function: a client that normalised its
    # address and then appended a path of its own would still build the doubled
    # url, and it is the client that posts.
    profiles = (
        ("prompt", agent.Client("http://127.0.0.1:1/v1/chat/completions")),
        ("hybrid", hybrid.Client("http://127.0.0.1:1/v1/chat/completions/")),
    )
    for label, client in profiles:
        if client.base_url != "http://127.0.0.1:1":
            failures.append(f"the {label} client holds {client.base_url}, not the service root")
    if len({client.base_url for _label, client in profiles}) != 1:
        failures.append("the two live profiles disagree about what a service root is")


POLICY_PHP = HARNESS / "lib" / "SandboxPolicy.php"


def _php_specs() -> list[dict]:
    """The registry's tool table, read out of the PHP rather than trusted.

    One summary is not a literal: `run_command`'s is its own text concatenated
    with `SandboxPolicy::summary()`, so the policy list is appended here from
    `SandboxPolicy.php` exactly as the PHP appends it. Reading it any other way
    would compare the solver against half of the harness's sentence.
    """
    text = REGISTRY_PHP.read_text(encoding="utf-8")
    documented = _php_documented_commands()
    specs = []
    for block in re.finditer(r"\[\s*'name'\s*=>\s*'([a-z_]+)',(.*?)\n            \],", text, re.S):
        body = block.group(2)
        summary = re.search(r"'summary'\s*=>\s*'((?:[^'\\]|\\.)*)'", body)
        required = re.search(r"'required'\s*=>\s*\[([^\]]*)\]", body)
        rendered = summary.group(1) if summary else ""
        if "SandboxPolicy::summary()" in body:
            # The PHP writes this summary as a concatenation of two literals and
            # the policy's list. Both literals are read back so the comparison is
            # against the whole sentence the harness renders, not the first part
            # of it; the words in between are the harness's own and are used
            # verbatim rather than reconstructed from a guess at them.
            between = re.search(r"\.\s*'([^']*Allowed tools:[^']*)'", body)
            prefix = between.group(1) if between else " Allowed tools: "
            rendered = rendered.rstrip() + prefix.rstrip() + " " + ", ".join(documented)
        specs.append(
            {
                "name": block.group(1),
                "summary": rendered,
                "required": re.findall(r"'([a-z_]+)'", required.group(1)) if required else [],
            }
        )
    return specs


def _php_documented_commands() -> list[str]:
    text = POLICY_PHP.read_text(encoding="utf-8")
    block = re.search(r"DOCUMENTED_BINARIES\s*=\s*\[(.*?)\];", text, re.S)
    return re.findall(r"'([a-z]+)'", block.group(1)) if block else []


def _php_prompt(budget: int, menu: str) -> str:
    """`AgentPrompt::prompted()`'s heredoc, with the menu and budget placed."""
    text = PROMPT_PHP.read_text(encoding="utf-8")
    body = re.search(r"return <<<TEXT\n(.*?)\n        TEXT;", text, re.S).group(1)
    dedented = "\n".join(line[8:] if line.startswith(" " * 8) else line for line in body.splitlines())
    return dedented.replace("{$menu}", menu).replace("{$stepBudget}", str(budget))


def _php_menu(specs: list[dict]) -> str:
    lines = []
    for spec in specs:
        arguments = ", ".join('"' + name + '"' for name in spec["required"])
        lines.append(f"- {spec['name']}: {{{arguments}}} - {spec['summary']}")
    return "\n".join(lines)


class ScriptedClient:
    """A client that answers with a fixed list of turns, so no model is needed."""

    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.sent: list[list[dict]] = []
        self.turns = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0

    def complete(self, messages: list[dict]) -> str:
        self.sent.append(list(messages))
        self.turns += 1
        return self.replies.pop(0) if self.replies else "{}"


#: The correct sequence for each task, as a solver that knows the answer emits
#: it. These are written here rather than imported from the converter so that
#: this test can fail for a reason the converter does not share.
CORRECT: dict[str, list[dict]] = {
    "create_exact_file": [
        {"action": "tool", "tool": "write_file", "args": {"path": "release.txt", "content": "name=atlas\nversion=3\n"}},
        {"action": "finish", "answer": ""},
    ],
    "count_by_extension": [
        {"action": "tool", "tool": "search_files", "args": {"pattern": "data/*.log"}},
        {"action": "tool", "tool": "write_file", "args": {"path": "count.txt", "content": "4"}},
        {"action": "finish", "answer": ""},
    ],
    "sum_two_files": [
        {"action": "tool", "tool": "read_file", "args": {"path": "a.txt"}},
        {"action": "tool", "tool": "read_file", "args": {"path": "b.txt"}},
        {"action": "tool", "tool": "write_file", "args": {"path": "total.txt", "content": "42"}},
        {"action": "finish", "answer": ""},
    ],
    "recover_misnamed_file": [
        {"action": "tool", "tool": "list_files", "args": {"path": "."}},
        {"action": "tool", "tool": "read_file", "args": {"path": "service.yml"}},
        {"action": "tool", "tool": "write_file", "args": {"path": "output.txt", "content": "port: 9090\nhost: localhost\n"}},
        {"action": "finish", "answer": ""},
    ],
    "extract_field_map": [
        {"action": "tool", "tool": "read_file", "args": {"path": "notes.txt"}},
        {"action": "tool", "tool": "write_file", "args": {"path": "parsed.json", "content": '{"alpha": 1, "beta": 2, "gamma": 3}'}},
        {"action": "finish", "answer": ""},
    ],
    "prune_by_extension": [
        {"action": "tool", "tool": "list_files", "args": {"path": "."}},
        {"action": "tool", "tool": "delete_file", "args": {"path": "session_a.tmp"}},
        {"action": "tool", "tool": "delete_file", "args": {"path": "session_b.tmp"}},
        {"action": "tool", "tool": "delete_file", "args": {"path": "session_c.tmp"}},
        {"action": "finish", "answer": ""},
    ],
    "follow_typos": [
        {"action": "tool", "tool": "read_file", "args": {"path": "notes.md"}},
        {"action": "tool", "tool": "write_file", "args": {"path": "lines.txt", "content": "7"}},
        {"action": "finish", "answer": ""},
    ],
    "organize_directory": [
        {"action": "tool", "tool": "make_directory", "args": {"path": "docs"}},
        {"action": "tool", "tool": "run_command", "args": {"command": "mv a.txt b.txt c.txt docs/"}},
        {"action": "finish", "answer": ""},
    ],
    "refuse_impossible": [
        {"action": "tool", "tool": "list_files", "args": {"path": "."}},
        {
            "action": "finish",
            "answer": "I cannot find deployment.json in the workspace root, so I cannot read its region field.",
        },
    ],
}


def _turns(task_id: str) -> list[str]:
    return [
        json.dumps(entry, separators=(",", ":")) if entry["action"] == "tool" else json.dumps(entry, separators=(",", ":"))
        for entry in CORRECT[task_id]
    ]


def check_registry(failures: list[str]) -> None:
    specs = _php_specs()
    if not specs:
        failures.append("the harness tool table could not be read from ToolRegistry.php")
        return
    declared = {spec["name"]: tuple(spec["required"]) for spec in specs}
    local = {name: required for name, _, required in agent.TOOL_SPECS}
    if declared != local:
        for name in sorted(set(declared) | set(local)):
            if declared.get(name) != local.get(name):
                failures.append(
                    f"{name}: the harness declares {declared.get(name)} and the solver {local.get(name)}"
                )

    documented = _php_documented_commands()
    if documented and tuple(documented) != prompt_shell.DOCUMENTED_COMMANDS:
        failures.append(
            "the bridge's command list is not the policy's: "
            f"{list(prompt_shell.DOCUMENTED_COMMANDS)} against {documented}"
        )

    rendered = agent.prompt_menu()
    expected = _php_menu(specs)
    if rendered != expected:
        for index, (mine, theirs) in enumerate(zip(rendered.splitlines(), expected.splitlines())):
            if mine != theirs:
                failures.append(f"menu line {index}: solver {mine!r} against harness {theirs!r}")
                break
        else:
            failures.append("the menu has a different number of lines than the harness renders")


def check_prompt(failures: list[str]) -> None:
    specs = _php_specs()
    if not specs:
        return
    for budget in (4, 8, 12):
        mine = agent.system_prompt(budget)
        theirs = _php_prompt(budget, _php_menu(specs))
        if mine != theirs:
            failures.append(f"the system prompt for a budget of {budget} is not the harness's text")
            break
    digest = hashlib.sha256(agent.system_prompt(8).encode("utf-8")).hexdigest()
    if agent.system_prompt(8) != agent.system_prompt(8):
        failures.append("the system prompt is not stable between calls")
    if len(digest) != 64:
        failures.append("the system prompt could not be digested")


def check_reading(failures: list[str]) -> None:
    cases = [
        ('{"action": "tool", "tool": "read_file", "args": {"path": "a.txt"}}', "tool", "read_file"),
        ('```json\n{"action": "tool", "tool": "read_file", "args": {"path": "a.txt"}}\n```', "tool", "read_file"),
        ('I will look at the file.\n{"action": "tool", "tool": "read_file", "args": {"path": "a.txt"}}', "tool", "read_file"),
        ('{"tool": "list_files", "path": "."}', "tool", "list_files"),
        ('{"action": "finish", "answer": "done"}', "finish", "finish"),
        ('{"answer": "no deployment.json here"}', "finish", "finish"),
    ]
    for text, kind, tool in cases:
        turn = agent.read_turn(text)
        if turn["kind"] != kind or turn["tool"] != tool:
            failures.append(f"{text[:40]!r} read as {turn['kind']}/{turn['tool']}, expected {kind}/{tool}")

    for text in ("I cannot do that.", "", "{}"):
        turn = agent.read_turn(text)
        if turn["kind"] != "invalid":
            failures.append(f"{text!r} read as {turn['kind']}, expected invalid")

    flat = agent.read_turn('{"tool": "write_file", "path": "x.txt", "content": "hi"}')
    if flat["args"] != {"path": "x.txt", "content": "hi"}:
        failures.append(f"a flattened call read as {flat['args']}")


def check_bridge(failures: list[str]) -> None:
    bridge = prompt_shell.ShellBridge()

    sandbox = Sandbox()
    try:
        sandbox.write("a.txt", "one\n")
        sandbox.write("b.txt", "two\n")
        sandbox.path("docs").mkdir()
        ok, _ = bridge.run("mv a.txt b.txt docs/", sandbox)
        if not ok or sandbox.exists("a.txt") or not sandbox.exists("docs/a.txt"):
            failures.append("the bridge did not carry out the move the suite's correct sequence uses")

        ok, observation = bridge.run("grep -rn two .", sandbox)
        if not ok or "docs/b.txt" not in observation:
            failures.append(f"the bridge did not find a line it should have: {observation!r}")

        ok, _ = bridge.run("wc -l docs/a.txt", sandbox)
        if not ok:
            failures.append("the bridge refused wc on a file that exists")

        for command, why in (
            ("sed -i 's/one/two/' docs/a.txt", "a word outside the policy"),
            ("mv a.txt docs/ | cat", "a pipeline"),
            ("rm -rf /etc", "an argument the policy refuses"),
            ("find . -name '*.txt' -exec cat {} ;", "program execution"),
        ):
            ok, observation = bridge.run(command, sandbox)
            if ok:
                failures.append(f"the bridge carried out {command!r}, which is {why}: {observation!r}")

        # The workspace is the only place a command can reach, so the refusal of
        # an absolute path is checked against the sandbox rather than the host.
        try:
            sandbox.exists("/etc/passwd")
        except Exception:
            pass
        else:
            failures.append("the workspace accepted an absolute path, so a command could leave it")
    finally:
        sandbox.cleanup()


def _php_grammar() -> str:
    """`ActionSchema::gbnf()`, rendered by the harness's own PHP.

    The grammar is what a constrained run sends to the sampler, so a port that
    differs from the harness by one character is a different condition wearing
    the harness's name. It is rendered rather than read because the PHP builds
    the string from the registry, and the point of comparing is to catch a port
    that has drifted from the source it claims to follow.
    """
    php = shutil.which("php")
    if php is None:
        return ""
    program = (
        'require "lib/SandboxPolicy.php"; require "lib/ToolRegistry.php"; '
        'require "lib/ActionSchema.php"; echo ActionSchema::gbnf();'
    )
    result = subprocess.run(
        [php, "-r", program], cwd=str(HARNESS), capture_output=True, text=True
    )
    return result.stdout if result.returncode == 0 else ""


def check_grammar(failures: list[str]) -> None:
    names = grammar.action_names([name for name, _, _ in agent.TOOL_SPECS])
    mine = grammar.gbnf(names)
    bare = grammar.gbnf(names, quoted=False)

    if "finish" in names:
        failures.append("the grammar offers finish as a tool name as well as its own layout")

    # The repair is one escaped pair, so it is pinned rather than described. A
    # tool name emitted as a bare GBNF literal produces
    # `{"action":"tool","tool":write_file,...}`, which json_decode refuses, and a
    # constrained run then refuses every turn of every task. That was measured
    # across five quants and nine tasks: 0 of 9 passed with protocol_compliance
    # 0.000, against 5 to 8 of 9 with no constraint at all. Two facts keep it
    # fixed: the two renderings have to differ, and the one the harness ships has
    # to emit the name inside JSON quotes.
    if bare == mine:
        failures.append("the bare rendering is identical to the harness's, so the repair is not in place")
    else:
        rule = next((line for line in mine.splitlines() if line.startswith("tool-name ::=")), "")
        if not names:
            failures.append("the grammar was asked for with no tool names")
        elif f'\\"{names[0]}\\"' not in rule:
            failures.append(
                "the grammar's tool-name rule does not emit the name inside JSON quotes, "
                "so a constrained sampler writes an object json_decode refuses"
            )

    expected = _php_grammar()
    if not expected:
        print("note: php is not installed, so the grammar port was not compared")
        return
    if mine != expected:
        for index, (ours, theirs) in enumerate(zip(mine.splitlines(), expected.splitlines())):
            if ours != theirs:
                failures.append(f"grammar line {index}: solver {ours!r} against harness {theirs!r}")
                break
        else:
            failures.append(
                f"the grammar has {len(mine.splitlines())} lines against the harness's "
                f"{len(expected.splitlines())}"
            )


def check_shell(failures: list[str]) -> None:
    """The documented words, and the pipeline the prompt promises."""
    bridge = prompt_shell.ShellBridge()

    sandbox = Sandbox()
    try:
        sandbox.write("data/app.log", "boot\nready\n")
        sandbox.write("data/db.log", "start\n")
        sandbox.write("data/worker-a.log", "idle\n")
        sandbox.write("data/worker-b.log", "busy\n")
        sandbox.write("notes.txt", "keep\n")

        ok, observation = bridge.run("find data/*.log | wc -l", sandbox)
        if not ok or "4" not in observation:
            failures.append(f"a pipeline the model actually wrote did not count: {observation!r}")

        ok, observation = bridge.run("ls *.txt", sandbox)
        if not ok or "notes.txt" not in observation:
            failures.append(f"ls with a glob did not match: {observation!r}")

        ok, observation = bridge.run("grep -rn boot data", sandbox)
        if not ok or "data/app.log" not in observation:
            failures.append(f"grep -rn did not find a line it should have: {observation!r}")

        sandbox.path("docs").mkdir()
        ok, _ = bridge.run("mv data/*.log docs/", sandbox)
        if not ok or sandbox.exists("data/app.log") or not sandbox.exists("docs/app.log"):
            failures.append("mv with a glob did not move every match")

        # A flag the bridge does not carry out must be refused, not ignored. The
        # cost of ignoring one was measured: `find . -name "*.tmp" -delete` was
        # answered "exit code 0", deleted nothing, and failed a task whose
        # command was correct.
        sandbox.write("scratch/one.tmp", "x\n")
        sandbox.write("scratch/two.tmp", "x\n")
        sandbox.write("scratch/keep.txt", "x\n")
        ok, observation = bridge.run('find scratch -type f -name "*.tmp" -delete', sandbox)
        if not ok:
            failures.append(f"find -delete was refused: {observation!r}")
        elif sandbox.exists("scratch/one.tmp") or sandbox.exists("scratch/two.tmp"):
            failures.append("find -delete reported success and deleted nothing")
        elif not sandbox.exists("scratch/keep.txt"):
            failures.append("find -delete removed a file its pattern did not match")
        elif "one.tmp" in observation:
            failures.append(f"find -delete printed names a real find does not: {observation!r}")

        for command in ("find . -maxdepth 2 -name '*.tmp'", "find . -mtime -1"):
            ok, observation = bridge.run(command, sandbox)
            if ok:
                failures.append(f"the bridge carried out {command!r}, a flag it does not implement: {observation!r}")

        for command, why in (
            ("sed -i 's/one/two/' notes.txt", "a word outside the policy"),
            ("cat notes.txt > copy.txt", "a redirect"),
            ("find . -name '*.txt' -exec cat {} ;", "program execution"),
        ):
            ok, observation = bridge.run(command, sandbox)
            if ok:
                failures.append(f"the bridge carried out {command!r}, which is {why}: {observation!r}")

        try:
            sandbox.exists("/etc/passwd")
        except Exception:
            pass
        else:
            failures.append("the workspace accepted an absolute path, so a command could leave it")
    finally:
        sandbox.cleanup()


def check_decoder(failures: list[str]) -> None:
    """The decoder is a condition, and the two conditions must be distinguishable."""
    plain = agent.Client("http://127.0.0.1:1", decoder=agent.DECODER_NONE)
    constrained = agent.Client("http://127.0.0.1:1", decoder=agent.DECODER_GRAMMAR)
    if plain.grammar:
        failures.append("the unconstrained client carries a grammar")
    if not constrained.grammar:
        failures.append("the grammar client carries no grammar")
    if agent.DEFAULT_MAX_TOKENS != 768:
        failures.append(f"the turn cap is {agent.DEFAULT_MAX_TOKENS}, not the harness's 768")
    if agent.DEFAULT_MAX_OBSERVATION != 3000:
        failures.append(f"the observation cap is {agent.DEFAULT_MAX_OBSERVATION}, not the harness's 3000")
    try:
        agent.Client("http://127.0.0.1:1", decoder="schema")
    except ValueError:
        pass
    else:
        failures.append("an unknown decoder name was accepted")


def check_suite(failures: list[str]) -> None:
    """A solver that knows the answer must pass every verifier."""
    for task in suite_module.suite(suite_module.SUITE_ALL):
        client = ScriptedClient(_turns(task["id"]))
        solver = agent.Solver(client, agent.Executor())
        sandbox = Sandbox()
        try:
            task["setup"](sandbox)
            trajectory = solver.solve(
                {"id": task["id"], "goal": task["goal"], "budget": task["budget"]}, sandbox
            )
            checks = task["verify"](
                sandbox, {"answer": trajectory["answer"], "finished": trajectory["finished"]}
            )
        finally:
            sandbox.cleanup()
        failed = [item["name"] for item in checks if not item["passed"]]
        if failed:
            failures.append(f"{task['id']}: the correct sequence failed {failed}")
        if not trajectory["finished"]:
            failures.append(f"{task['id']}: the correct sequence never reached a closing turn")


def check_profile(failures: list[str]) -> None:
    """A scripted endpoint must produce a run the writer and the reader accept."""
    module = suite_module
    tasks = module.suite(module.SUITE_ALL)

    class PerTaskClient:
        def __init__(self) -> None:
            self.current = ""
            self.turns = 0
            self.prompt_tokens = 0
            self.completion_tokens = 0

        def complete(self, messages: list[dict]) -> str:
            self.turns += 1
            for task in tasks:
                if task["goal"] == messages[1]["content"]:
                    self.current = task["id"]
                    break
            replies = _turns(self.current)
            index = sum(1 for entry in messages if entry["role"] == "assistant")
            return replies[index] if index < len(replies) else '{"action": "finish", "answer": ""}'

        def health(self) -> bool:
            return True

    from gembench import reference

    reference.unregister_profile("scripted-check")
    reference.register_profile("scripted-check", agent.behaviours(PerTaskClient()))
    result = runner.run("scripted-check", module.SUITE_ALL, "scripted-check", "scripted-check")
    reference.unregister_profile("scripted-check")

    if result["summary"]["tasks_passed"] != len(tasks):
        failures.append(
            f"a solver that knows every answer passed {result['summary']['tasks_passed']} of {len(tasks)}"
        )
    findings = runner.check(result)
    if findings:
        failures.extend(findings)


def main() -> int:
    if not REGISTRY_PHP.is_file():
        print(f"no harness at {HARNESS}: the registry and prompt checks cannot run")
        return 3

    failures: list[str] = []
    check_registry(failures)
    check_prompt(failures)
    check_reading(failures)
    check_grammar(failures)
    check_shell(failures)
    check_decoder(failures)
    check_endpoint(failures)
    check_suite(failures)
    check_profile(failures)

    for finding in failures:
        print(f"[error] {finding}")
    if failures:
        return 2
    digest = hashlib.sha256(agent.system_prompt(8).encode("utf-8")).hexdigest()[:16]
    print(
        "ok: the tool table, the system prompt (sha256 %s), the reader, the bridge and all "
        "%d verifiers agree with the harness" % (digest, len(suite_module.suite(suite_module.SUITE_ALL)))
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
