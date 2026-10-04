#!/usr/bin/env python3
"""
NLP smoke test.

Checks the interpretation layer without loading the model: file resolution,
chunk planning and merge, prompt and tool-call repair, the confined navigator,
linter selection, and context assembly. Every check runs against a throwaway
tree, so it is safe to run any time and fast enough to gate a session before the
GGUF is ever pulled into memory.

Run directly or through nlp_smoke.sh. Exit status is non-zero when a check
fails, which makes it usable as a benchmark gate.
"""
from __future__ import annotations

import io
import sys
import tempfile
import time
from pathlib import Path

from rich.console import Console

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import splash  # noqa: E402
import chat_mode  # noqa: E402
from core import adapter, chunk_merge, linters  # noqa: E402
from core.interpreter import FileIndex  # noqa: E402
from core.workspace_shell import Navigator, WorkspaceError  # noqa: E402
from engine import context as context_builder, prompt_repair  # noqa: E402
from engine import orchestrator  # noqa: E402
from engine.events import NULL_REPORTER  # noqa: E402
from config import HARNESS_DIR, IGNORE_DIRS, NAV_ROOT, TARGET_ROOT, VERSION, WORKSPACE  # noqa: E402
from chat_mode import ChatSession, ChatTranscript, Clipboard  # noqa: E402
from theme import themed_console  # noqa: E402

SHADOW_COPY_DEFAULT = orchestrator.SHADOW_COPY
ORIGINAL_COPY_DEFAULT = orchestrator.ORIGINAL_COPY

_APP = """\
def add(a, b):
    return a + b


def subtract(a, b):
    return a - b


def multiply(a, b):
    return a * b
"""

_LOGIN = """\
<?php
session_start();
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    $user = $_POST['user'] ?? '';
    if ($user === 'admin') {
        $_SESSION['user'] = $user;
        header('Location: /');
    }
}
"""


class Recorder:
    """Collects PASS/FAIL lines and per-group timings."""

    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0

    def check(self, label: str, condition: bool) -> None:
        if condition:
            self.passed += 1
            print(f"  PASS  {label}")
        else:
            self.failed += 1
            print(f"  FAIL  {label}")

    def group(self, name: str, start: float) -> None:
        print(f"[{name}] {(time.perf_counter() - start) * 1000:.1f}ms\n")


def _fixture(root: Path) -> None:
    (root / "auth").mkdir(parents=True, exist_ok=True)
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "docs").mkdir(parents=True, exist_ok=True)
    (root / "auth" / "login.php").write_text(_LOGIN, encoding="utf-8")
    (root / "src" / "app.py").write_text(_APP, encoding="utf-8")
    (root / "src" / "admin_panel.py").write_text(_APP, encoding="utf-8")
    (root / "README.md").write_text("# demo\n", encoding="utf-8")
    big = "".join(f"line_{i} = {i}\n" for i in range(400))
    (root / "src" / "big.py").write_text(big, encoding="utf-8")


def test_interpretation(root: Path, rec: Recorder) -> None:
    start = time.perf_counter()
    index = FileIndex(root)
    exact = index.resolve("src/app.py")
    rec.check("exact path resolves", exact.green and exact.path == "src/app.py")
    rec.check("exact kind is exact", exact.kind == "exact")

    cwd = index.resolve("login.php", cwd="auth")
    rec.check("cwd-relative name resolves", cwd.green and cwd.path == "auth/login.php")

    base = index.resolve("big.py")
    rec.check("bare basename resolves", base.green and base.path == "src/big.py")

    stem = index.resolve("admin_panel")
    rec.check("stem resolves", stem.green and stem.path == "src/admin_panel.py")

    fuzzy = index.resolve("src/ap.py")
    rec.check("closest neighbour resolves", fuzzy.green and fuzzy.path in ("src/app.py", "src/big.py"))
    rec.check("fuzzy reports confidence", 0 < fuzzy.confidence <= 1.0)

    missing = index.resolve("does_not_exist_anywhere.py")
    rec.check("unmatched is not green-lit", not missing.green)
    rec.check("unmatched offers candidates", isinstance(missing.candidates, list))

    glob = index.resolve("*.php")
    rec.check("glob resolves", glob.green and glob.path.endswith(".php"))

    mentions = index.mentions("please fix auth/login.php and src/app.py", limit=5)
    paths = {m.path for m in mentions}
    rec.check("sentence mentions found", {"auth/login.php", "src/app.py"} <= paths)
    rec.check("mentions are green", all(m.green for m in mentions))
    rec.group("interpretation", start)


def test_chunk_merge(root: Path, rec: Recorder) -> None:
    start = time.perf_counter()
    plan = chunk_merge.plan("src/big.py", root=root, max_chars=400, overlap_lines=20)
    rec.check("large file chunks", plan.chunked and len(plan.chunks) > 1)
    if len(plan.chunks) > 1:
        first, second = plan.chunks[0], plan.chunks[1]
        rec.check("windows overlap", second.start_line <= first.end_line)
    rec.check("render labels ranges", 'chunk="lines ' in chunk_merge.render_plan(plan))

    edited = [(plan.chunks[0], plan.chunks[0].text.replace("line_0 = 0", "line_0 = 99"))]
    merged, _diff, conflicts = chunk_merge.merge_edit_results(
        (root / "src" / "big.py").read_text(encoding="utf-8"), edited
    )
    rec.check("merge applies an edit", "line_0 = 99" in merged)
    rec.check("merge keeps later lines", "line_399 = 399" in merged)
    rec.check("merge returns a diff", "+line_0 = 99" in _diff)

    # Both windows change the same overlap line: the merge must report it.
    text = (root / "src" / "big.py").read_text(encoding="utf-8")
    plan2 = chunk_merge.plan("src/big.py", root=root, max_chars=400, overlap_lines=30)
    if len(plan2.chunks) >= 2:
        first, second = plan2.chunks[0], plan2.chunks[1]
        overlap_line = first.end_line
        tampered_first = first.text
        tampered_second = second.text.replace(
            f"line_{overlap_line} = {overlap_line}", "OVERLAP_EDIT"
        )
        _content, _diff, conflicts = chunk_merge.merge_edit_results(
            text, [(first, tampered_first.replace("line_1 = 1", "A_EDIT")), (second, tampered_second)]
        )
        rec.check("overlap conflict detected", bool(conflicts))
    rec.group("chunk_merge", start)


def test_prompt_repair(rec: Recorder) -> None:
    start = time.perf_counter()
    fenced = '```json\n{"edits": [{"filepath": "a.py", "old_str": "", "new_str": "x = 1"}]}\n```'
    edits = prompt_repair.coerce_edits(fenced)
    rec.check("fenced JSON parsed", len(edits) == 1 and edits[0]["filepath"] == "a.py")

    aliased = [{"path": "a.py", "old_string": "x", "new_string": "y"}]
    edits = prompt_repair.coerce_edits(aliased)
    rec.check("aliased keys mapped", edits and edits[0]["old_str"] == "x" and edits[0]["new_str"] == "y")

    single = {"filepath": "a.py", "content": "print(1)"}
    edits = prompt_repair.coerce_edits(single)
    rec.check("single edit object accepted", len(edits) == 1 and edits[0]["new_str"] == "print(1)")

    prose = prompt_repair.repair_arguments('here you go: {"instruction": "x", "edits": []}')
    rec.check("prose around JSON parsed", prose.get("instruction") == "x")

    rec.check("trailing comma survived", prompt_repair.extract_json('{"a": [1,2,],}') is not None)

    normalized = prompt_repair.normalize_tool_arguments(
        {"task": "add a helper", "paths": "a.py", "changes": [{"file": "a.py", "new": "def f():\n    pass"}]}
    )
    rec.check("arguments normalized", normalized["instruction"] == "add a helper" and normalized["files"] == ["a.py"])
    rec.check("edits normalized", normalized["edits"][0]["filepath"] == "a.py")

    big = {"filepath": "a.py", "old_str": "", "new_str": "\n".join(str(i) for i in range(80))}
    hint = prompt_repair.minimal_lines_hint([big], limit=25)
    rec.check("oversized edit flagged", "25" in hint and "a.py" in hint)
    rec.check("small edit not flagged", prompt_repair.minimal_lines_hint(
        [{"filepath": "a.py", "old_str": "", "new_str": "x = 1"}], limit=25
    ) == "")

    traversal = prompt_repair.coerce_edits([{"filepath": "../../etc/passwd", "old_str": "", "new_str": "x"}])
    rec.check("traversal path preserved", bool(traversal) and traversal[0]["filepath"] == "../../etc/passwd")
    rec.check("leading ./ trimmed", prompt_repair.coerce_edit({"filepath": "./a.py", "new_str": "x"})["filepath"] == "a.py")

    boiler = prompt_repair.boilerplate_hint(
        [{"filepath": "a.php", "old_str": "", "new_str": "<!DOCTYPE html>\n<html>\n<body>x"}]
    )
    rec.check("boilerplate flagged on a create", "a.php" in boiler and "minimal-code" in boiler)
    rec.check("edit against an existing file not flagged", prompt_repair.boilerplate_hint(
        [{"filepath": "a.php", "old_str": "x", "new_str": "<!DOCTYPE html>"}]
    ) == "")
    rec.check("write with a small stub old_str flagged", "minimal-code" in prompt_repair.boilerplate_hint(
        [{"filepath": "a.php", "old_str": "<?php\n", "new_str": "<!DOCTYPE html>\n<html>\n" + "<div>x</div>\n" * 8}]
    ))
    rec.check("clean create not flagged", prompt_repair.boilerplate_hint(
        [{"filepath": "a.php", "old_str": "", "new_str": "<?php\n$u = $_POST['u'];\n?>\n<form method=post></form>"}]
    ) == "")

    # The observed failure: valid JSON whose old_str swallowed the envelope tail.
    leaked = prompt_repair.coerce_edits(
        {"edits": [{"filepath": "auth/login.php", "new_str": "<form>", "old_str": '""}],files:['}]}
    )
    rec.check("leaked old_str salvaged", bool(leaked) and leaked[0]["old_str"] == "" and leaked[0]["repaired"])
    rec.check("implausible filepath dropped", prompt_repair.coerce_edits(
        [{"filepath": "auth login.py", "old_str": "", "new_str": "x"}]
    ) == [])
    rec.check("real code with a bracket not treated as a leak", prompt_repair.coerce_edits(
        [{"filepath": "a.py", "old_str": "data = [{\"a\": 1}]", "new_str": "data = [{\"a\": 2}]"}]
    )[0]["old_str"] == "data = [{\"a\": 1}]")

    wrapped = "<!DOCTYPE html>\n<html>\n<head>\n<title>L</title>\n</head>\n<body>\n<form method=post>x</form>\n</body>\n</html>\n"
    edits = [{"filepath": "a.php", "old_str": "", "new_str": wrapped}]
    changed = prompt_repair.enforce_minimal(edits)
    rec.check("document wrapper stripped", changed == 1 and "<!doctype" not in edits[0]["new_str"].lower())
    rec.check("inner markup kept", "<form method=post>x</form>" in edits[0]["new_str"])
    rec.check("surgical edit untouched", prompt_repair.enforce_minimal(
        [{"filepath": "a.php", "old_str": "<body>x</body>", "new_str": "<body>y</body>"}]
    ) == 0)

    # Contract: these helpers take edit objects, not the whole request object.
    request = prompt_repair.normalize_tool_arguments({
        "instruction": "make a form",
        "edits": [{"filepath": "a.php", "old_str": "", "new_str": "<!DOCTYPE html>\n<html>\n" + "x\n" * 40}],
    })
    rec.check("flattened edits flag boilerplate", "minimal-code" in prompt_repair.boilerplate_hint(request["edits"]))
    rec.check("a request object is not an edit list", prompt_repair.boilerplate_hint([request]) == "")

    # The observed failure: the generation stopped at the token ceiling inside
    # the JSON envelope, so only the first completed edit survived. Nothing was
    # staged, and the terminal still looked like the turn had finished.
    cut_off = (
        '{"instruction": "make a php login system", "files": ["login.php", "process.php"], "edits": ['
        '{"filepath": "login.php", "old_str": "", "new_str": "<form method=post></form>"},'
        '{"filepath": "process.php", "old_str": "", "new_str": "<?php\\n$u = $_POST['
    )
    salvaged = prompt_repair.normalize_tool_arguments(prompt_repair.repair_arguments(cut_off))
    rec.check("a call cut off mid-JSON still yields the edit that finished",
              len(salvaged["edits"]) == 1 and salvaged["edits"][0]["filepath"] == "login.php")
    rec.check("the salvaged edit keeps its body",
              salvaged["edits"][0]["new_str"] == "<form method=post></form>")
    rec.check("the cut-off call keeps its instruction",
              salvaged["instruction"] == "make a php login system")
    rec.check("the cut-off call keeps the file list",
              salvaged["files"] == ["login.php", "process.php"])

    rec.check("a call cut off before any edit closes has nothing to salvage",
              prompt_repair.repair_arguments('{"edits": [{"filepath": "a.py", "new_str": "<?php') == {})
    rec.check("a complete envelope is not re-read as a single edit",
              len(prompt_repair.normalize_tool_arguments(prompt_repair.repair_arguments(
                  '{"instruction": "x", "files": ["a.py", "b.py"], "edits": ['
                  '{"filepath": "a.py", "old_str": "", "new_str": "x = 1"},'
                  '{"filepath": "b.py", "old_str": "", "new_str": "y = 2"}]}'
              ))["edits"]) == 2)

    # A cut-off pass must be described as a cut-off, not as broken syntax: the
    # retry then asks for a smaller file instead of repeating the same one.
    rec.check("a length stop is reported as a cut-off call",
              prompt_repair.failure_reason("length", "arguments were not valid JSON")
              == "the call was cut off before its JSON closed")
    rec.check("a length stop with no call is reported as a cut-off",
              prompt_repair.failure_reason("length") == "the response was cut off")
    rec.check("a stop that never called the tool says so",
              prompt_repair.failure_reason("stop") == "the model did not call the tool")
    rec.check("a parse failure keeps its own reason",
              prompt_repair.failure_reason("stop", "no usable edits were present")
              == "no usable edits were present")
    rec.group("prompt_repair", start)


def test_path_guard(root: Path, rec: Recorder) -> None:
    start = time.perf_counter()
    refused = 0
    for attempt in ("../../etc/passwd", "/etc/passwd"):
        try:
            adapter.resolve(attempt, root)
        except adapter.AdapterError:
            refused += 1
    rec.check("resolver refuses paths outside the root", refused == 2)
    inside = adapter.resolve("src/app.py", root)
    rec.check("resolver allows a path inside the root", inside.exists())
    rec.group("path_guard", start)


def test_navigator(root: Path, rec: Recorder) -> None:
    start = time.perf_counter()
    nav = Navigator(root)
    rec.check("starts at root", nav.pwd() == ".")
    nav.cd("src")
    rec.check("cd into a directory", nav.pwd() == "src" and nav.prompt_path() == "~/src")
    rec.check("ls lists files", any(e.name == "app.py" for e in nav.ls()))
    nav.cd("..")
    rec.check("cd back to root", nav.pwd() == ".")

    escaped = False
    try:
        nav.cd("..")
    except WorkspaceError:
        escaped = True
    rec.check("cannot cd above the workspace", escaped and nav.pwd() == ".")

    home = False
    try:
        nav.cd("~")
    except WorkspaceError:
        home = True
    rec.check("cannot cd to home", home)

    outside = False
    try:
        nav.cd("/etc")
    except WorkspaceError:
        outside = True
    rec.check("cannot cd outside the workspace", outside)

    nav.cd("auth")
    resolution = nav.resolve("login.php")
    rec.check("navigator resolves in cwd", resolution.green and resolution.path == "auth/login.php")

    text, total, truncated = nav.cat("login.php")
    rec.check("cat reads the file", "session_start" in text and total > 0 and not truncated)

    missing = False
    try:
        nav.cat("nope.php")
    except WorkspaceError:
        missing = True
    rec.check("cat refuses a missing file", missing)
    rec.group("navigator", start)


def test_linters(root: Path, rec: Recorder) -> None:
    start = time.perf_counter()
    rec.check("python checker selected", bool(linters.commands_for("src/app.py")))
    rec.check("php checker selected", any("php" in cmd for cmd in linters.commands_for("auth/login.php")))

    broken = root / "src" / "broken.py"
    broken.write_text("def f(:\n    pass\n", encoding="utf-8")
    findings = linters.lint_file("src/broken.py", root=root)
    rec.check("syntax error detected", bool(findings))

    good = linters.lint_file("src/app.py", root=root)
    rec.check("clean file reports nothing", good == [])

    text = linters.format_diagnostics(
        [linters.Diagnostic("a.py", 3, 1, "E1", "bad thing")]
    )
    rec.check("diagnostics formatted", "a.py:3:1" in text and "bad thing" in text)
    rec.group("linters", start)


def test_context(root: Path, rec: Recorder) -> None:
    start = time.perf_counter()
    augmented, included, labels = context_builder.build_user_context(
        "fix auth/login.php please", root=root, cwd=""
    )
    rec.check("context attaches the file", "auth/login.php" in included)
    rec.check("context labels the match", "auth/login.php" in labels)
    rec.check("context body present", "session_start" in augmented)

    _aug, big_included, _labels = context_builder.build_user_context(
        "review src/big.py", root=root, budget=2000
    )
    rec.check("large file attaches in windows", "src/big.py" in big_included)

    small_root = Navigator(root)
    rec.check("navigator and index agree", small_root.resolve("README.md").path == "README.md")
    rec.group("context", start)


class _FakeRepairClient:
    """Stands in for the model: answers a repair request with the exact fix."""

    def __init__(self, fix: dict) -> None:
        self.fix = fix
        self.calls = 0

    def generate_repairs(self, instruction: str, context: str, diagnostics: str) -> list[dict]:
        self.calls += 1
        return [self.fix]


def test_repair_wiring(root: Path, rec: Recorder) -> None:
    start = time.perf_counter()
    shadow = root / "shadow"
    original = root / "original"
    orchestrator.SHADOW_COPY = shadow
    orchestrator.ORIGINAL_COPY = original

    staged, errors = orchestrator.stage_edits(
        [{"filepath": "broken.py", "old_str": "", "new_str": "def f(:\n    return 1\n", "regex": False}],
        root=root, stamp="smoke", lint=True,
    )
    rec.check("broken file staged", len(staged) == 1 and not errors)
    rec.check("syntax defect attached", bool(staged and staged[0].get("diagnostics")))

    if staged and staged[0].get("diagnostics"):
        client = _FakeRepairClient({"filepath": "broken.py", "old_str": "def f(:", "new_str": "def f():"})
        orchestrator._repair_staged(client, "fix the syntax", staged, {}, root, NULL_REPORTER)
        rec.check("repair used the model once", client.calls >= 1)
        rec.check("defect cleared by repair", not staged[0].get("diagnostics"))
        rec.check("repaired content is valid", "def f():" in staged[0]["new_content"])

    orchestrator.SHADOW_COPY = SHADOW_COPY_DEFAULT
    orchestrator.ORIGINAL_COPY = ORIGINAL_COPY_DEFAULT
    rec.group("repair_wiring", start)


def test_workspace_root(root: Path, rec: Recorder) -> None:
    """The workspace is one root: navigator, adapter and agent agree on it."""
    start = time.perf_counter()

    rec.check("agent root is the workspace", TARGET_ROOT == WORKSPACE)
    rec.check(
        "navigator root is inside the workspace",
        NAV_ROOT == WORKSPACE or WORKSPACE in NAV_ROOT.parents,
    )
    rec.check("harness store sits inside the workspace", WORKSPACE in HARNESS_DIR.parents)
    rec.check("harness store is excluded from walks", HARNESS_DIR.name in IGNORE_DIRS)

    # An ignored name in an ancestor must not hide the tree; only a folder
    # inside the root that carries the name is ignored. This is the bug that
    # made a workspace below a folder named build look empty.
    nested = root / "build" / "proj"
    (nested / "build").mkdir(parents=True, exist_ok=True)
    (nested / "app.py").write_text("x = 1\n", encoding="utf-8")
    (nested / "build" / "gen.py").write_text("y = 2\n", encoding="utf-8")

    rec.check("an ignored ancestor name does not hide the root",
              not adapter.is_ignored(nested / "app.py", nested))
    rec.check("an ignored folder inside the root is hidden",
              adapter.is_ignored(nested / "build" / "gen.py", nested))
    rec.check("a path outside the root is never walked",
              adapter.is_ignored(root / "auth" / "login.php", nested))
    rec.check("find_files keeps the rest of the tree",
              any(entry.rel_path == "app.py" for entry in adapter.find_files("**/*.py", root=nested)))
    rec.check("find_files skips the ignored folder",
              all("build/gen.py" not in entry.rel_path for entry in adapter.find_files("**/*.py", root=nested)))

    harness = root / ".xcalibur"
    (harness / "jobs").mkdir(parents=True, exist_ok=True)
    guarded = Navigator(root, harness=harness)
    refused = False
    try:
        guarded.cd(".xcalibur")
    except WorkspaceError:
        refused = True
    rec.check("navigator refuses the harness store", refused and guarded.pwd() == ".")
    rec.check("harness store is not listed", all(".xcalibur" not in entry.name for entry in guarded.ls()))
    rec.check("workspace contents are listed", any(entry.name == "README.md" for entry in guarded.ls()))
    rec.group("workspace_root", start)


def _render(renderable, width: int = 96) -> str:
    """
    Render a Rich renderable to plain text, so a frame can be asserted on.

    No terminal is forced, so no escape sequences are written and the checks
    compare the words and the layout rather than the colours.
    """
    console = Console(width=width, record=True, file=io.StringIO())
    with console.capture() as capture:
        console.print(renderable)
    return capture.get()


def test_splash(rec: Recorder) -> None:
    start = time.perf_counter()
    art = splash.load_art()
    rec.check("banner art loads", art.rows >= 10 and art.cols >= 20)
    rec.check("version placeholder substituted", splash.VERSION_TOKEN not in art.stamped())
    rec.check("version stamped into the art", VERSION in art.stamped())
    rec.check("moon block located by shape", len(art.moon_rows) >= 4)
    rec.check("moon rows are contiguous", sorted(art.moon_rows) == list(range(min(art.moon_rows), max(art.moon_rows) + 1)))

    rec.check("gradient is not flat", splash.ramp(0.0) != splash.ramp(1.0))
    rec.check("gradient endpoints come from the palette", splash.ramp(0.0) == splash.GRADIENT[0])
    rec.check("mix midpoint sits between the ends", splash.ramp(0.0) != splash.mix(splash.GRADIENT[0], splash.GRADIENT[1], 0.5))
    rec.check("sink darkens", splash.sink("#ffffff", 0.5) != "#ffffff")
    rec.check("clamp holds the range", splash.clamp(5.0) == 1.0 and splash.clamp(-3.0) == 0.0)

    steps = [
        splash.Step("workspace", "workspace", lambda: None),
        splash.Step("model", "local model server", lambda: None),
        splash.Step("dashboard", "review dashboard", lambda: None),
    ]
    steps[0].mark("done", summary="workspace")
    steps[1].mark("done", summary="http://127.0.0.1:8080")
    steps[2].mark("running")

    first = _render(splash.frame(art, steps, 0.0))
    second = _render(splash.frame(art, steps, 0.5))
    settled = _render(splash.frame(art, steps, 3.0))
    rec.check("frames change over time", first != second)
    rec.check("reveal grows downward", len(settled.splitlines()) >= len(first.splitlines()))
    rec.check("settled frame shows every character", sum(len(line.strip()) for line in settled.splitlines()) > 0)
    rec.check("step summary rendered", "http://127.0.0.1:8080" in settled)
    rec.check("running step rendered", "review dashboard" in settled)
    rec.check("done mark rendered", "+ workspace" in settled)
    rec.check("moon phase tick rendered", any(phase in settled for phase in splash.PHASES))
    rec.check("progress bar rendered", "█" in settled or "░" in settled)
    rec.check("frame carries no version token", splash.VERSION_TOKEN not in settled)
    rec.check("narrow terminal falls back to a compact banner", "Xcalibur" in _render(splash.frame(art, steps, 3.0), width=20))

    with tempfile.TemporaryDirectory(prefix="xcalibur-splash-") as tmp:
        missing = splash.load_art(Path(tmp) / "absent.txt")
        rec.check("missing art degrades", missing.rows >= 1 and "Xcalibur" in missing.stamped())

    rec.check("plain banner is unanimated", VERSION in _render(splash.plain_banner()))

    # Boot in plain mode (no terminal here): order, results, and failure handling.
    order: list[str] = []
    boot = splash.Boot(enabled=True, hold_seconds=0.0)
    boot.add("one", "first", lambda: order.append("one") or "one-ok", lambda v: v)
    boot.add("two", "second", lambda: (_ for _ in ()).throw(RuntimeError("nope")), lambda v: v)
    boot.add("three", "third", lambda: order.append("three") or "three-ok", lambda v: v)
    rec.check("no terminal means no animation", not boot.animate(Console(file=io.StringIO())))
    results = boot.run(Console(file=io.StringIO(), record=True))
    rec.check("steps ran in order", order == ["one", "three"])
    rec.check("results keyed by step", results["one"] == "one-ok" and results["three"] == "three-ok")
    rec.check("failed step records None", results["two"] is None)
    rec.check("failure does not stop the boot", boot.steps[2].status == "done")
    rec.check("failed step carries the reason", boot.steps[1].status == "failed" and "nope" in boot.steps[1].error)
    rec.check("splash can be switched off", not splash.Boot(enabled=False).animate(Console(force_terminal=True)))
    rec.group("splash", start)


class _FakeChatClient:
    """A model client that answers from a script and records what it was asked."""

    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.tools: list[object] = []
        self.seen: list[list[dict]] = []

    def chat(self, messages, tools=None, **kwargs):
        self.tools.append(tools)
        self.seen.append(list(messages))
        reply = self._replies.pop(0) if self._replies else ""

        return {"choices": [{"message": {"role": "assistant", "content": reply},
                             "finish_reason": "stop"}]}


class _ScriptedPrompter:
    """Feeds a chat session the lines a person would have typed."""

    def __init__(self, lines: list[str]) -> None:
        self._lines = list(lines)
        self.asked: list[str] = []

    def read(self, label: str) -> str:
        self.asked.append(label)

        return self._lines.pop(0) if self._lines else "/end"


class _RecordingCopier:
    """Stands in for a clipboard, so a copy can be asserted rather than performed."""

    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.written: list[str] = []

    def copy(self, text: str) -> "chat_mode.CopyResult":
        self.written.append(text)

        return chat_mode.CopyResult(self.ok, "recorded")


def test_chat_mode(rec: Recorder) -> None:
    start = time.perf_counter()
    quiet = themed_console(file=io.StringIO())

    transcript = ChatTranscript()
    rec.check("an empty transcript has nothing to render or copy",
              transcript.render() == "" and transcript.empty() and transcript.last_reply() == "")
    transcript.add("you", "hello")
    transcript.add("model", "hi there")
    rec.check("the transcript names both speakers",
              "you: hello" in transcript.render() and "model: hi there" in transcript.render())
    rec.check("the last reply is the answer, not the question", transcript.last_reply() == "hi there")
    transcript.clear()
    rec.check("clearing empties the transcript", transcript.empty())

    with tempfile.TemporaryDirectory(prefix="xcalibur-chat-") as tmp:
        piped: list[str] = []

        def record_pipe(command, text):
            piped.append(text)
            return True

        clipboard = Clipboard(tools=(("pbcopy",),), directory=tmp, runner=record_pipe,
                              which=lambda name: "/usr/bin/" + name)
        result = clipboard.copy("hello there")
        rec.check("a platform clipboard is used when it exists",
                  result.ok and "pbcopy" in result.message)
        rec.check("the text was piped to the tool", piped == ["hello there"])

        refused = Clipboard(tools=(("pbcopy",),), directory=tmp, runner=lambda c, t: False,
                            which=lambda name: "/usr/bin/" + name).copy("second try")
        written = sorted(Path(tmp).glob("chat-*.txt"))
        rec.check("a tool that refuses falls through to a file", refused.ok and len(written) == 1)
        rec.check("the written file holds the text",
                  written[0].read_text(encoding="utf-8") == "second try")

        fallback = Clipboard(tools=(("absent",),), directory=tmp, which=lambda name: None)
        rec.check("no clipboard tool at all still copies, by file",
                  fallback.copy("third").ok and len(sorted(Path(tmp).glob("chat-*.txt"))) == 2)
        both = sorted(Path(tmp).glob("chat-*.txt"))
        rec.check("two copies in the same second do not overwrite each other",
                  len(both) == 2 and {path.read_text(encoding="utf-8") for path in both}
                  == {"second try", "third"})
        rec.check("nothing to copy is a refusal, not an empty success",
                  not Clipboard(tools=(), which=lambda name: None).copy("   ").ok)

    # The loop: two turns, a slash command that copies, and the offer on leaving.
    client = _FakeChatClient(["first answer", "second answer"])
    prompter = _ScriptedPrompter(["hello", "and again", "/copy", "/end", "y"])
    copier = _RecordingCopier()
    ChatSession(client, prompter, copier=copier, sink=quiet).run()

    rec.check("each typed line reached the model once", len(client.seen) == 2)
    rec.check("no tool is offered in direct chat", client.tools == [None, None])
    rec.check("the conversation opens with the chat system prompt",
              client.seen[0][0]["role"] == "system"
              and "direct chat" in client.seen[0][0]["content"])
    rec.check("a later turn carries the earlier answer",
              len(client.seen[1]) == 4 and client.seen[1][2]["content"] == "first answer")
    rec.check("/copy takes the last reply", bool(copier.written) and copier.written[0] == "second answer")
    rec.check("leaving asks whether to copy the transcript",
              prompter.asked[-1].startswith("copy the transcript"))
    rec.check("saying yes copied the whole chat",
              len(copier.written) == 2 and "hello" in copier.written[-1]
              and "second answer" in copier.written[-1])

    # A session that never offers has no question to answer, and an unknown slash
    # command is reported rather than sent to the model as a message.
    second_client = _FakeChatClient(["ok"])
    second_prompter = _ScriptedPrompter(["/wat", "hi", "/end"])
    ChatSession(second_client, second_prompter, copier=_RecordingCopier(),
                sink=themed_console(file=io.StringIO()), offers_copy=False).run()
    rec.check("an unknown chat command is not sent to the model", len(second_client.seen) == 1)
    rec.check("a session that does not offer leaves without asking",
              all(not label.startswith("copy") for label in second_prompter.asked))

    # The dispatcher itself: the chat command has to be reachable from the prompt,
    # and the slash form has to mean the same thing as the bare one.
    import cli  # imported here so the harness's own startup is not exercised by a smoke test

    rec.check("a command may be typed with a leading slash", cli._command_of("/chat") == "chat")
    rec.check("the bare form is the same command", cli._command_of("chat") == cli._command_of("/chat"))
    rec.check("the slash form is case insensitive too", cli._command_of("/CHAT") == "chat")
    rec.check("arguments are kept out of the command word",
              cli._command_of("/chat about a router") == "chat")
    rec.check("a lone slash is no command at all", cli._command_of("/") == "")
    rec.check("chat mode is offered under both spellings it is dispatched by",
              "chat" in cli.HELP and "/chat" in cli.HELP)
    rec.group("chat_mode", start)


def main() -> int:
    print("XcaliburLite NLP smoke test\n")
    rec = Recorder()
    overall = time.perf_counter()

    with tempfile.TemporaryDirectory(prefix="xcalibur-smoke-") as tmp:
        root = Path(tmp)
        _fixture(root)
        test_interpretation(root, rec)
        test_chunk_merge(root, rec)
        test_prompt_repair(rec)
        test_path_guard(root, rec)
        test_navigator(root, rec)
        test_workspace_root(root, rec)
        test_linters(root, rec)
        test_context(root, rec)
        test_repair_wiring(root, rec)

    test_splash(rec)
    test_chat_mode(rec)

    total = (time.perf_counter() - overall) * 1000
    print(f"passed {rec.passed}, failed {rec.failed} in {total:.0f}ms")
    return 0 if rec.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
