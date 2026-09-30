"""Reads the artefacts a repository document is built from.

This is the reading half of `build_readme.py`, kept apart from the half that
writes prose because the two have different failure modes. A reading is wrong
when an artefact has moved or changed shape, and that is caught by running it
against the tree. A paragraph is wrong when it stops being true, and that is
caught by a person. Mixing them puts the guessable half and the checkable half
in one function.

Nothing here formats and nothing here decides what a document looks like. Every
function answers a question about the tree: what the weight files say they are,
which runs were recorded, what a run measured and under which controls.
"""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent

if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
if str(HERE / "kaggle") not in sys.path:
    sys.path.insert(0, str(HERE / "kaggle"))

import gguf  # noqa: E402

try:  # the portable benchmark, which owns the suite and the arithmetic
    from gembench import suite  # noqa: E402
except ImportError:  # a checkout without it still gets a document, with less in it
    suite = None  # type: ignore[assignment]

#: The weight directory, the prefix that marks a supporting file rather than a
#: chat model, and where the recorded reading is kept. Declared here so the
#: reader and the document cannot describe the same file differently.
MODELS_DIR = Path("models")
PROJECTOR_PREFIX = "mmproj"
SNAPSHOT_PATH = Path("results/models/headers.json")

SIDES_PATH = Path("results/benchmark/sides.json")
SIDES_CSV_PATH = Path("results/benchmark/sides.csv")

LICENCE_PATH = Path("LICENSE")
DESKTOP_MANIFEST = Path("desktop/package.json")
IGNORE_PATH = Path(".gitignore")

#: An environment name, which is what a release reads a credential from. The
#: uppercase names a signing script uses are read out of the script rather than
#: listed here, so a name that changes in the script changes in the document.
ENVIRONMENT_NAME = re.compile(r"['\"]([A-Z][A-Z0-9_]{2,})['\"]")

#: The variable that turns a reported problem into a stopped build.
RELEASE_SWITCH = re.compile(r"const\s+REQUIRE\s*=\s*'([A-Z0-9_]+)'")


def read_text(path: Path) -> str:
    """A file's text, or the empty string when it cannot be read."""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def read_json(path: Path) -> dict | None:
    """A JSON file as a mapping, or None when it is absent or malformed."""
    text = read_text(path)
    if not text:
        return None
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, dict) else None


# ---------------------------------------------------------------------------
# The weight files.
# ---------------------------------------------------------------------------


def default_model_names(root: Path) -> dict[str, str]:
    """The weight file names the configuration points at by default.

    Read out of `config.php` rather than restated, because the role a row plays
    in a model table has to be the role the runtime gives it, and a second copy
    of that relationship is a second copy that can disagree.
    """
    text = read_text(root / "config.php")
    names: dict[str, str] = {}
    for constant, key in (("GEMMA_GGUF_PATH", "model"), ("GEMMA_MMPROJ_PATH", "projector")):
        match = re.search(
            rf"define\('{constant}'.*?GEMMA_MODELS_DIR\s*\.\s*'/([^']+\.gguf)'",
            text,
            re.DOTALL,
        )
        if match:
            names[key] = match.group(1)
    return names


def read_models(root: Path, record: bool = True) -> tuple[list[dict], str]:
    """The model set, and where the reading came from.

    Live headers are preferred and are written to the record; the record answers
    on a checkout that does not hold the weights, which is the state the
    repository is in by default. The two sources are named rather than merged,
    so the document can say which one it used.
    """
    directory = root / MODELS_DIR
    live: list[dict] = []
    if directory.is_dir():
        for header in gguf.read_headers(directory):
            path = directory / Path(header.path).name
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            live.append({"file": path.name, "bytes": size, **header.summary()})

    if live:
        if record:
            write_model_record(root, live)
        return live, "the weight files on this machine"

    recorded = read_json(root / SNAPSHOT_PATH)
    if recorded:
        headers = recorded.get("headers", [])
        if isinstance(headers, list) and headers:
            return headers, "the recorded reading in " + str(SNAPSHOT_PATH)

    return [], "nothing: no weights on this machine and no recorded reading"


def write_model_record(root: Path, models: list[dict]) -> Path:
    """Record the reading, so a checkout without the weights can describe them."""
    destination = root / SNAPSHOT_PATH
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "document": "model-headers",
                "note": (
                    "The metadata block of each weight file as read on the machine that holds "
                    "them. The weights are a local input and are not in this repository; this "
                    "record is what lets a document describe them on a checkout that does not "
                    "have them."
                ),
                "directory": str(MODELS_DIR),
                "headers": models,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return destination


def quantisation_disagreements(models: list[dict]) -> list[str]:
    """Files whose name and header do not say the same quantisation."""
    problems: list[str] = []
    for model in models:
        name = str(model.get("file", ""))
        named = gguf.quant_word(name)
        recorded = str(model.get("quantisation", ""))
        if named and recorded and named != recorded:
            problems.append(
                f"`{name}` is named {named} but its header says {recorded} "
                f"(file_type {model.get('file_type')})"
            )
    return problems


# ---------------------------------------------------------------------------
# The recorded runs.
# ---------------------------------------------------------------------------


def csv_rows(path: Path) -> list[dict]:
    """A run's CSV as records keyed by column name."""
    if not path.is_file():
        return []
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))
    except (OSError, csv.Error):
        return []


def number(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def suite_of(task_ids: list[str]) -> str:
    """Which named suite a run measured, by comparing the task ids.

    Derived rather than read from the manifest, because the manifest is where a
    run states what it meant to measure and the CSV is what it actually wrote.
    The two disagreeing is the interesting case.
    """
    if suite is None or not task_ids:
        return "-"
    measured = set(task_ids)
    for name in (suite.SUITE_ALL, suite.SUITE_CORE, suite.SUITE_LEVELS):
        if measured == set(suite.ids(name)):
            return name
    return "mixed"


def run_dirs(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(child for child in directory.iterdir() if child.is_dir())


def run_summary(run_dir: Path) -> dict | None:
    """One recorded run, read from its manifest and its CSV.

    The pass count and the composite come from the walk when a summary recorded
    them, and are recomputed from the rows when it did not, so a run written by
    either the harness or the portable benchmark is read the same way.
    """
    manifest = read_json(run_dir / "manifest.json")
    if manifest is None:
        return None

    run = manifest.get("run", {})
    rows = csv_rows(run_dir / "tasks.csv")

    task_ids = [row.get("task_id", "") for row in rows if row.get("task_id")]
    if not task_ids:
        task_ids = [task.get("id", "") for task in manifest.get("tasks", [])]

    summary = manifest.get("summary") or {}
    if summary.get("tasks"):
        tasks = int(summary["tasks"])
        passed = int(summary.get("tasks_passed", 0))
        composite = number(summary.get("composite"))
    else:
        tasks = len(rows)
        passed = sum(1 for row in rows if number(row.get("success")) >= 1)
        composite = sum(number(row.get("composite")) for row in rows) / len(rows) if rows else 0.0

    models = sorted(manifest.get("models", {}).keys())
    if not models and run.get("model"):
        models = [str(run["model"])]

    controls = manifest.get("controls", {})
    return {
        "run_id": str(run.get("run_id", run_dir.name)),
        "models": models,
        "tasks": tasks,
        "passed": passed,
        "composite": composite,
        "tool_mode": str(run.get("tool_mode", "-")),
        "suite": suite_of(task_ids),
        "loop_guard": controls.get("loop_guard"),
        "strict_schema": controls.get("strict_schema"),
        "sandbox_policy": str(controls.get("sandbox_policy", "-")),
    }


def agent_runs(root: Path) -> list[dict]:
    """Every run under results/agent that left a manifest and a CSV."""
    summaries = []
    for run_dir in run_dirs(root / "results" / "agent"):
        summary = run_summary(run_dir)
        if summary is not None:
            summaries.append(summary)
    return summaries


# ---------------------------------------------------------------------------
# The two sides of the portable benchmark.
# ---------------------------------------------------------------------------


def sides(root: Path) -> dict | None:
    """The joined reading the comparison step wrote."""
    return read_json(root / SIDES_PATH)


def agreement(root: Path) -> tuple[int, int]:
    """How many (solver, task) rows every side agreed on, out of how many.

    The comparison joins the sides by run name, so the rows to count are the
    pairs of name and task. A row only counts when every side measured it and
    all of them reported the same composite.
    """
    rows = csv_rows(root / SIDES_CSV_PATH)
    if not rows:
        return 0, 0

    by_solver: dict[str, dict[str, dict[str, float]]] = {}
    for row in rows:
        column = row.get("column", "")
        if "/" not in column:
            continue
        side, solver = column.split("/", 1)
        task = row.get("task_id", "")
        if not task:
            continue
        by_solver.setdefault(solver, {}).setdefault(side, {})[task] = number(row.get("composite"))

    agreed = 0
    total = 0
    for per_side in by_solver.values():
        if len(per_side) < 2:
            continue
        names = sorted(per_side)
        for task in per_side[names[0]]:
            values = [per_side[name].get(task) for name in names[1:]]
            if any(value is None for value in values):
                continue
            total += 1
            baseline = per_side[names[0]][task]
            if all(abs(value - baseline) < 1e-6 for value in values):
                agreed += 1

    return agreed, total


# ---------------------------------------------------------------------------
# The license and the distribution.
# ---------------------------------------------------------------------------


def _desktop_manifest(root: Path) -> dict:
    """The desktop application's manifest, or an empty mapping."""
    return read_json(root / DESKTOP_MANIFEST) or {}


def author_name(manifest: dict) -> str:
    """The name a manifest records as the author, whether it is a string or a mapping."""
    author = manifest.get("author", "")
    if isinstance(author, dict):
        return str(author.get("name", "")).strip()
    return str(author).strip()


def licence_holder(text: str) -> str:
    """The holder the last copyright line in a license file names.

    The last one wins because a license file ends with the appendix that was
    filled in for this work, while any copyright line above it belongs to the
    license text itself.
    """
    found = re.findall(r"^\s*Copyright(?:\s+\(c\))?\s+\d{4}\s+(.+?)\s*$", text, re.MULTILINE)
    return found[-1].strip() if found else ""


def licence(root: Path) -> dict:
    """What the repository says about the terms it is offered under.

    Two files make the claim and neither is trusted over the other: the manifest
    declares an identifier and an author, and the file carries the text and a
    copyright line. This reads both and reports whether they agree, which is the
    only thing a document can honestly say about a license without a lawyer.
    """
    text = read_text(root / LICENCE_PATH)
    manifest = _desktop_manifest(root)
    holder = licence_holder(text)
    author = author_name(manifest)

    return {
        "file": str(LICENCE_PATH),
        "present": bool(text),
        "lines": len(text.splitlines()),
        "spdx": str(manifest.get("license", "")),
        "holder": holder,
        "author": author,
        "agrees": holder != "" and holder == author,
    }


def _env_names(text: str) -> list[str]:
    """The uppercase names a script reads, in the order it names them."""
    seen: list[str] = []
    for match in ENVIRONMENT_NAME.finditer(text):
        name = match.group(1)
        if name not in seen:
            seen.append(name)
    return seen


def distribution(root: Path) -> dict:
    """How the desktop application is signed, as the configuration declares it.

    Nothing here signs anything. The configuration states what a build was asked
    to do, the files it names are checked for being on disk, and the names a
    release reads a credential from are read out of the hook rather than listed
    here. `lib/DistributionCheck.php` asks the same files the stricter question,
    of whether the entitlements carry what the runtime needs; this answers only
    what a document should say.
    """
    manifest = _desktop_manifest(root)
    build = manifest.get("build", {}) if isinstance(manifest.get("build"), dict) else {}
    mac = build.get("mac", {}) if isinstance(build.get("mac"), dict) else {}
    directories = build.get("directories", {}) if isinstance(build.get("directories"), dict) else {}
    scripts = manifest.get("scripts", {}) if isinstance(manifest.get("scripts"), dict) else {}

    hook = str(build.get("afterSign", ""))
    verify = str(scripts.get("verify", ""))
    entitlements = [str(mac.get(key, "")) for key in ("entitlements", "entitlementsInherit")]
    entitlements = [name for name in entitlements if name]

    hook_text = read_text(root / "desktop" / hook) if hook else ""
    switch = RELEASE_SWITCH.search(hook_text)
    switch_name = switch.group(1) if switch else ""
    names = [name for name in _env_names(hook_text) if name != switch_name]

    return {
        "app_id": str(build.get("appId", "")),
        "product": str(manifest.get("productName", "")),
        "version": str(manifest.get("version", "")),
        "hardened": mac.get("hardenedRuntime") is True,
        "gatekeeper_assess": mac.get("gatekeeperAssess") is True,
        "minimum_system": str(mac.get("minimumSystemVersion", "")),
        "entitlements": entitlements,
        "entitlements_counts": {
            name: read_text(root / "desktop" / name).count("<key>") for name in entitlements
        },
        "entitlements_missing": [
            name for name in entitlements if not (root / "desktop" / name).is_file()
        ],
        "hook": hook,
        "hook_present": bool(hook) and (root / "desktop" / hook).is_file(),
        "hook_names": names,
        "hook_switch": switch_name,
        "verify_script": verify,
        "output": str(directories.get("output", "")),
        "scripts": sorted(scripts),
    }
