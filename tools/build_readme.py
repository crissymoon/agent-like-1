"""Writes README.md, which is the repository describing itself.

The document a person reads first should be the one thing in the tree that
cannot go stale, so it is not written by hand. Every number in it is read at
build time from the artefact that owns it:

  - the models table is the metadata block of each GGUF file in `models/`, read
    from the header rather than typed, and cross-checked against the
    quantisation spelled in the file's own name;
  - the suite table is the task definitions in the portable benchmark, so a task
    added there appears here without an edit;
  - the scoring table is the weight table the runtime and the portable benchmark
    are both held against;
  - the results tables are the recorded runs under `results/`, summarised by
    column name so a run written by either side is read the same way.

The weights are a local input and are not committed, so the reading is recorded
to `results/models/headers.json` as it is taken. Where the weights are present
the table comes from the files; on a plain checkout it comes from that record.
The document names which of the two it used, so a reader is never shown a table
whose provenance is a guess.

Reading lives in `tools/readme_source.py`; this file turns what it reads into
prose. That split is deliberate, because a wrong reading and a stale sentence
are caught by different things.

    python3 tools/build_readme.py            # write README.md
    python3 tools/build_readme.py --check    # fail when README.md is out of date

The check compares the document on disk with what the builder would write now,
apart from the one sentence naming the machine the model reading came from. That
sentence is the only part of the document that legitimately differs between a
checkout holding the weights and one that is not, and a check that failed for
that reason would be a check people learn to ignore.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent

if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import readme_source as source  # noqa: E402

try:  # the portable benchmark, which owns the suite and the arithmetic
    from gembench import scoring, suite  # noqa: E402
except ImportError:  # a checkout without it still gets a document, with less in it
    scoring = None  # type: ignore[assignment]
    suite = None  # type: ignore[assignment]

README_NAME = "README.md"

#: One line per top-level entry. The reason is the point of the line, and an
#: entry that is not on disk is left out rather than described in the abstract.
LAYOUT: tuple[tuple[str, str], ...] = (
    ("agent.php", "Runs the agent tasks, one suite at a time, and writes the run."),
    ("ladder.php", "Compares a recorded run against a baseline and reports the movement."),
    ("stream.php", "Streams a run's events so a person can watch an episode."),
    ("compare.php", "Reads two recorded runs and reports where they differ."),
    ("benchmark.php", "Lists the local weight files and reads recorded runs into one table."),
    ("lib/", "The harness itself: the loop, the tools, the sandbox, the scoring, the reports."),
    ("docker/", "The pinned engine image and the compose file that mounts one weight file."),
    ("desktop/", "The Electron view over a run directory; it drives the same harness, never a copy."),
    ("tools/", "Portable tooling: the benchmark that runs on both sides, the kernel builder, the release review, this builder."),
    ("local-model-quick-tester/", "The interactive front end: a menu over the same GGUF weights, and the markdown transcript of a run, written to a directory held out of version control."),
    ("mermaid-viewer/", "A diagram viewer: renders the .mmd files in the directory with the mermaid build vendored beside it, offline, with pan, zoom and SVG or PNG export."),
    ("results/", "What each run left behind. Evidence, committed, so a claim has a file behind it."),
    ("local-benchmarking.md", "The ladder: what a rung is, what decides it, and what a result does not claim."),
    ("LICENSE", "The terms the work is offered under, and the holder it belongs to."),
)


# ---------------------------------------------------------------------------
# Small formatting helpers. A table cell is the only place a value is changed.
# ---------------------------------------------------------------------------


def cell(value) -> str:
    """A value as it may appear inside a markdown table cell."""
    return " ".join(str(value).replace("|", "\\|").split()) or "-"


def table(headers: list[str], rows: list[list]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    lines.extend("| " + " | ".join(cell(value) for value in row) + " |" for row in rows)
    return lines


def byte_label(value: int) -> str:
    if value <= 0:
        return "-"
    if value >= 1024**3:
        return f"{value / 1024**3:.2f} GB"
    return f"{value / 1024**2:.1f} MB"


def context_label(value) -> str:
    """A context window, with the round number beside it for readability."""
    if not isinstance(value, int):
        return "-"
    if value >= 1024 and value % 1024 == 0:
        return f"{value:,} ({value // 1024}K)"
    return f"{value:,}"


def plain_count(value) -> str:
    return f"{value:,}" if isinstance(value, int) else "-"


def first_sentence(text: str, limit: int = 150) -> str:
    """One sentence of a long task statement, ended with a full stop."""
    flat = " ".join(str(text).split())
    match = re.search(r"\.(?:\s|$)", flat)
    sentence = flat[: match.end()].strip() if match else flat
    if len(sentence) > limit:
        cut = sentence[:limit].rsplit(" ", 1)[0]
        return cut.rstrip(",;:") + "..."
    return sentence


def flag(value) -> str:
    if value is None:
        return "not recorded"
    return "on" if value else "off"


# ---------------------------------------------------------------------------
# The sections.
# ---------------------------------------------------------------------------


def layout_section(root: Path) -> list[str]:
    lines = ["## What is here", ""]
    rows = [
        [name, reason] for name, reason in LAYOUT if (root / name.rstrip("/")).exists()
    ]
    lines.extend(table(["path", "what it is"], rows))
    lines.append("")
    return lines


def mechanism_section() -> list[str]:
    return [
        "## How a run works",
        "",
        "A task declares a goal, a workspace and a step budget. The loop hands the model one "
        "action at a time in a fixed protocol, applies the action inside a jail that allows an "
        "explicit list of commands, and folds the result back into the transcript with the "
        "oldest turns dropped once the context is full. When the budget runs out or the model "
        "stops, the task's own verifier reads the workspace and returns a pass or a fail with "
        "a reason.",
        "",
        "Nothing about the model is trusted. Tool calls are checked against a schema before "
        "anything is executed, an observation is clipped before it re-enters the context, and "
        "the score is computed from the counters the loop recorded rather than from the "
        "model's account of itself.",
        "",
    ]


def models_section(root: Path, models: list[dict], origin: str) -> list[str]:
    lines = ["## Models under test", ""]
    lines.append(
        "The weights are a local input. They are mounted read only into the engine and they are "
        "not in this repository, so the set of models is a reading of `models/` rather than a "
        "list anybody maintains, and a selection is a name checked against that reading rather "
        "than a path somebody typed. This table was built from "
        f"{origin}. Every cell comes from the file's own GGUF header: the architecture, the size "
        "label, the context window and the layer count are what the writer recorded, not what a "
        "download page said."
    )
    lines.append("")

    if not models:
        lines.append(
            "No model reading was available when this document was written, so the table is "
            "empty. Place the weight files in `models/` and run the builder to fill it."
        )
        lines.append("")
        return lines

    defaults = source.default_model_names(root)
    projector_prefix = source.PROJECTOR_PREFIX

    # An image model does not belong in this table at all. It cannot answer a
    # benchmark task, so a row of dashes beside a language model would invite the
    # reader to compare two things that are not comparable. Which files are image
    # models is read out of the catalog the image tool itself resolves against,
    # so the split cannot drift from what that tool will actually run.
    image_files = source.image_model_files(root)
    encoder_files = source.text_encoder_files(root)
    chat = [
        m
        for m in models
        if not str(m.get("file", "")).startswith(projector_prefix)
        and str(m.get("file", "")) not in image_files
        and str(m.get("file", "")) not in encoder_files
    ]
    support = [m for m in models if str(m.get("file", "")).startswith(projector_prefix)]
    # A file the encoder discovery claims is named here rather than in the table
    # above. The names come from the same reading the image tool performs, so the
    # document and the tool cannot disagree about which file is in use.
    encoders = [m for m in models if str(m.get("file", "")) in encoder_files]
    selected = defaults.get("model", "")

    # The selected model first, then by file name: the order the runtime uses, so
    # the row a reader already knows is the row at the top.
    chat.sort(key=lambda m: (str(m.get("file")) != selected, str(m.get("file", ""))))

    rows = [
        [
            model.get("file", ""),
            model.get("architecture", ""),
            model.get("size_label", "") or plain_count(model.get("parameter_count")),
            model.get("quantisation", ""),
            context_label(model.get("context_length")),
            plain_count(model.get("embedding_length")),
            plain_count(model.get("block_count")),
            byte_label(int(model.get("bytes", 0))),
            "the runtime's selected model" if model.get("file") == selected else "the comparison model",
        ]
        for model in chat
    ]
    lines.extend(
        table(
            [
                "file",
                "architecture",
                "size",
                "quantisation",
                "context",
                "embeddings",
                "layers",
                "on disk",
                "role",
            ],
            rows,
        )
    )
    lines.append("")

    if chat and chat[0].get("file") == selected:
        first = chat[0]
        lines.append(
            f"The runtime starts on `{first.get('file')}`, so it is the model every recorded run "
            "measured and the one a comparison starts from. A benchmark run swaps the weight file "
            "behind the same engine, the same tools and the same sandbox, which is what makes two "
            "rows comparable: the only thing that moved is the model."
        )
        lines.append("")

    if len(chat) > 1:
        labels = ", ".join(f"`{model.get('file')}`" for model in chat[1:])
        lines.append(
            f"The other files in the directory are the breadth of the study rather than a "
            f"preference: {labels} are the same measurement on a different architecture, and the "
            "point of running them is to see whether a result belongs to the harness or to one "
            "model. A capability that only the largest model shows is a property of the model; "
            "one that every model shows is a property of the task, and a task every model fails "
            "is the one worth rewriting."
        )
        lines.append("")

    if support:
        entries = ", ".join(
            f"`{model.get('file')}` ({model.get('architecture', '')}, "
            f"{model.get('quantisation', '')}, {byte_label(int(model.get('bytes', 0)))})"
            for model in support
        )
        lines.append(
            f"The projector is listed apart because it is not a chat model. {entries} maps image "
            "embeddings into the language model beside it, and the engine loads it only when a "
            "vision task is asked for. A benchmark that treated it as a candidate would spend a "
            "run proving that an embedding file cannot answer a question, so the model set "
            "excludes it and a run's manifest records whether it was loaded."
        )
        lines.append("")

    if encoders:
        entries = ", ".join(
            f"`{model.get('file', '')}` ({model.get('architecture', '')}, "
            f"{model.get('quantisation', '')}, {byte_label(int(model.get('bytes', 0)))})"
            for model in encoders
        )
        lines.append(
            f"The text encoder is listed apart for the same reason the projector is: it is not a "
            f"candidate. {entries} is a language model in its own right, and here it is the encoder "
            "an image pipeline conditions on rather than a chat model to answer a question, so it "
            "is a row of the image section below and not of this table. Running it as a candidate "
            "would measure it against tasks it is not asked to do."
        )
        lines.append("")

    if image_files:
        lines.extend(image_models_section(root))

    disagreements = source.quantisation_disagreements(chat + support)
    if disagreements:
        lines.append(
            "One reading did not agree with the file it came from, and is reported rather than "
            "smoothed over:"
        )
        lines.append("")
        lines.extend(f"- {line}" for line in disagreements)
        lines.append("")
    else:
        lines.append(
            "The quantisation in every file name agrees with the quantisation the header records, "
            "which is the cross-check that catches a renamed weight file before a run is "
            "attributed to the wrong one."
        )
        lines.append("")

    lines.append(
        "A context window is a property of the file and not a promise about the run: the engine "
        "is started with an explicit context setting, and the value it accepted is in "
        "`results/engine/engine-profile.json` beside the image digest it came from."
    )
    lines.append("")
    return lines


def image_models_section(root: Path) -> list[str]:
    """The diffusion models, read from their tensor tables rather than a header.

    Everything here is derived. The rows come from the tensor table of each
    weight file, the split between these and the language models comes from the
    catalog the tool resolves against, and the prose is assembled from those
    numbers, so a file that is added or replaced moves the document with it.
    """
    models, origin = source.read_image_models(root)
    if not models:
        return []

    lines = ["### Image models", ""]
    lines.append(
        "The directory also holds diffusion models, which generate an image rather than answer a "
        "question. They are listed apart because they are not candidates for the benchmark and "
        f"not comparable with the rows above. This table was built from {origin}. A diffusion "
        "GGUF describes very little of itself in its metadata block and one of these describes "
        "nothing at all, so every cell comes from the tensor table, which is what the loader "
        "reads: the block counts are the architecture, and the byte counts are the quantisation."
    )
    lines.append("")

    rows = []
    for model in models:
        blocks = model.get("blocks") or {}
        rows.append(
            [
                model.get("file", ""),
                model.get("label", ""),
                model.get("architecture", "") or "-",
                model.get("quantisation", "") or "-",
                " + ".join(str(count) for count in blocks.values()) or "-",
                plain_count(model.get("parameters")),
                byte_label(int(model.get("packed_bytes") or 0)),
                byte_label(int(model.get("exact_bytes") or 0)),
            ]
        )
    lines.extend(
        table(
            [
                "file",
                "model",
                "architecture",
                "quantisation",
                "blocks",
                "weights",
                "as stored",
                "if held exactly",
            ],
            rows,
        )
    )
    lines.append("")

    for model in models:
        packed = int(model.get("packed_bytes") or 0)
        exact = int(model.get("exact_bytes") or 0)
        if not packed or not exact:
            continue
        lines.append(
            f"`{model.get('file', '')}` carries {byte_label(packed)} of weights where holding the "
            f"same {plain_count(model.get('parameters'))} numbers exactly would take "
            f"{byte_label(exact)}, which is {packed / exact:.0%} of it. The weights stay packed "
            "the whole way: a quantised tensor is expanded a block at a time inside the forward "
            "pass rather than expanded once on load, so the byte column is what the process "
            "carries and the file size on disk is not."
        )
        lines.append("")

    repositories = ", ".join(
        f"`{model.get('repository', '')}`" for model in models if model.get("repository")
    )
    keys = ", ".join(f"`{model.get('key', '')}`" for model in models if model.get("key"))
    encoders = [model for model in models if model.get("text_encoder")]
    if encoders:
        lines.append(
            "One weight file is one component of a pipeline and not a pipeline. The diffusion "
            "transformer is loaded from the file and handed to a pipeline assembled from the base "
            "repository that goes with it, and that repository is where the VAE, the scheduler "
            f"and the tokenizer come from: {repositories}. The text encoder does not have to come "
            "from there, and that is the difference between a model that fits on a laptop and one "
            "that does not."
        )
    else:
        lines.append(
            "One weight file is one component of a pipeline and not a pipeline. The diffusion "
            "transformer is loaded from the file and handed to a pipeline assembled from the base "
            "repository that goes with it, and that repository is where the text encoder, the VAE "
            f"and the scheduler come from: {repositories}. That is where the memory actually goes, "
            "and it is the reason a model can be small on disk and still not run on a laptop."
        )
    lines.append("")
    lines.extend(text_encoder_section(encoders))
    lines.append(
        f"Which one runs is `--model` with a key: {keys}. `--list` reports every image model and "
        "every weight file no profile drives, and `--check` holds a file against the model that "
        "claims it, checks the quantisation against what the loader can expand, and prices the "
        "run against this machine, all before anything is downloaded."
    )
    lines.append("")
    return lines


def text_encoder_section(models: list[dict]) -> list[str]:
    """The quantised text encoders, and what holding one packed is worth.

    An encoder is a language model, so it is read and described the same way the
    transformer is: from the file's own tensor table. A model whose encoder could
    not be read says so here rather than being left out, because a file that was
    looked for and not found is a different fact from one that was never sought.
    """
    records = [(model, model.get("text_encoder") or {}) for model in models]
    read = [(model, record) for model, record in records if record.get("file")]
    unread = [(model, record) for model, record in records if record.get("unavailable")]
    if not read and not unread:
        return []

    lines = ["#### Text encoders", ""]
    lines.append(
        "The text encoder of the pipeline above is a language model in its own right, and it is "
        "usually the larger half of the two. A quantised file of that encoder can be held in "
        "place of the repository's own, and it is held packed: a quantised tensor is expanded a "
        "block at a time inside the forward pass, so the file costs what the file costs. The file "
        "is found by the architecture it declares in its own metadata, and it is checked against "
        "the base repository's own encoder config, field by field, before anything is loaded."
    )
    lines.append("")

    if read:
        rows = []
        for model, encoder in read:
            layers = ", ".join(str(layer) for layer in encoder.get("layers", []))
            name = encoder.get("class_name", "")
            rows.append(
                [
                    encoder.get("file", ""),
                    model.get("label", ""),
                    encoder.get("architecture", ""),
                    f"{name} layers {layers}" if layers else name,
                    " + ".join(str(kind) for kind in (encoder.get("type_histogram") or {})),
                    plain_count(encoder.get("parameters")),
                    byte_label(int(encoder.get("packed_bytes") or 0)),
                    byte_label(int(encoder.get("exact_bytes") or 0)),
                ]
            )
        lines.extend(
            table(
                [
                    "file",
                    "conditions",
                    "architecture",
                    "encoder",
                    "types",
                    "weights",
                    "as stored",
                    "if held exactly",
                ],
                rows,
            )
        )
        lines.append("")

    for model, encoder in read:
        packed = int(encoder.get("packed_bytes") or 0)
        exact = int(encoder.get("exact_bytes") or 0)
        if not packed or not exact:
            continue
        lines.append(
            f"`{encoder.get('file', '')}` carries {byte_label(packed)} of weights where holding "
            f"the same {plain_count(encoder.get('parameters'))} numbers exactly would take "
            f"{byte_label(exact)}, which is {packed / exact:.0%} of it. Read without that file the "
            f"encoder is the one in `{model.get('repository', '')}`, and it is the size in the last "
            "column: the file saves the memory, not the arithmetic, and what that costs is the "
            "precision of the conditioning rather than the size of the model."
        )
        lines.append("")

    for model, encoder in unread:
        lines.append(
            f"Whether `{model.get('label', '')}` has a quantised text encoder could not be "
            f"established: {encoder.get('unavailable')}."
        )
        lines.append("")
    return lines


def suite_section() -> list[str]:
    lines = ["## The benchmark suite", ""]
    if suite is None:
        lines.append(
            "The portable benchmark is not present in this checkout, so the task table could not "
            "be read. It lives in `tools/kaggle/gembench/`."
        )
        lines.append("")
        return lines

    lines.append(
        "Nine tasks, each of which a program can decide. A task is in the suite only if the end "
        "state can be checked from the outside: the verifier reads the filesystem, and on the "
        "stopping rule it reads the closing answer, because refusing correctly is that task's "
        "pass condition rather than a failure to finish it. What a solver says about its own "
        "work is never the evidence."
    )
    lines.append("")

    core_ids = suite.ids(suite.SUITE_CORE)
    level_ids = suite.ids(suite.SUITE_LEVELS)
    rows = [
        [
            task["id"],
            task["capability"],
            task["budget"],
            "core" if task["id"] in core_ids else "levels",
            first_sentence(task["goal"]),
        ]
        for task in suite.suite(suite.SUITE_ALL)
    ]
    lines.extend(table(["task", "capability", "step budget", "suite", "the task"], rows))
    lines.append("")
    lines.append(
        f"Two names select a subset: `core` is the {len(core_ids)} tasks every recorded run "
        f"measured, `levels` is the {len(level_ids)} added afterwards, and `all` is both. The "
        "split exists because a suite that grows silently makes an earlier result unreadable, so "
        "the runtime records which one it ran and a comparison refuses to join two runs that "
        "measured different sets."
    )
    lines.append("")
    lines.append(
        "The three later tasks test what the first six do not: whether an instruction survives "
        "noisy wording, whether a model can tidy a directory without destroying the parts of it "
        "it was not asked to touch, and whether a model stops and names a missing input instead "
        "of inventing one. The third is the one worth watching, because the failure mode it "
        "catches looks like success from the outside."
    )
    lines.append("")
    return lines


def scoring_section() -> list[str]:
    lines = ["## How a run is scored", ""]
    if scoring is None:
        lines.append(
            "The portable scoring module is not present in this checkout, so the weights could "
            "not be read. They live in `tools/kaggle/gembench/scoring.py`."
        )
        lines.append("")
        return lines

    lines.append(
        "A result is a weighted blend rather than a pass or a fail, because for a model this "
        "size the interesting fact is usually how it failed. Each dimension is computed from "
        "counters the loop recorded, so every number can be recomputed from the run's own CSV. "
        "The weights are declared once in the runtime and once in the portable benchmark, and a "
        "parity check refuses the build when the two disagree."
    )
    lines.append("")
    rows = [
        [name, f"{weight:.2f}", f"{weight * scoring.SCORE_SCALE:.0f}"]
        for name, weight in scoring.WEIGHTS.items()
    ]
    lines.extend(table(["dimension", "weight", "points of 100"], rows))
    lines.append("")
    lines.append(
        "Task success carries the most weight and is the only pass-or-fail term. Recovery is "
        "averaged only over the tasks that actually met an error, so a solver that never erred "
        "is neither rewarded nor punished for a problem it never had. Efficiency compares the "
        "steps taken with the budget the task declared, which is why a task's budget is part of "
        "its definition rather than a setting on the loop."
    )
    lines.append("")
    return lines


def agent_section(root: Path) -> list[str]:
    lines = ["### Runs against the local engine", ""]
    runs = source.agent_runs(root)
    if not runs:
        lines.append("No run with a manifest was found under `results/agent/`.")
        lines.append("")
        return lines

    rows = [
        [
            run["run_id"],
            ", ".join(run["models"]) or "-",
            run["tool_mode"],
            run["suite"],
            f"{run['passed']}/{run['tasks']}",
            f"{run['composite']:.2f}",
            flag(run["loop_guard"]),
            flag(run["strict_schema"]),
            run["sandbox_policy"],
        ]
        for run in runs
    ]
    lines.extend(
        table(
            [
                "run",
                "model",
                "tool protocol",
                "suite",
                "tasks passed",
                "composite",
                "loop guard",
                "strict schema",
                "sandbox",
            ],
            rows,
        )
    )
    lines.append("")
    lines.append(
        "Each row is a directory under `results/agent/`, and the controls beside it are the ones "
        "its own manifest recorded, so a result is never read apart from the conditions that "
        "produced it. The suite column is derived by comparing the run's task ids with the named "
        "suites rather than taken from the manifest, which is how a run that measured a "
        "different set is visible rather than assumed."
    )
    lines.append("")
    lines.append(
        "`ladder.php` reads one of these runs against a baseline and reports the movement, and "
        "`compare.php` reads two of them against each other. Both answer a question about a "
        "change in the harness; neither is a model scoreboard."
    )
    lines.append("")
    return lines


def sides_section(root: Path) -> list[str]:
    lines = ["### The same suite on two sides", ""]
    record = source.sides(root)
    if not record:
        lines.append(
            "No joined reading of the two sides was found at "
            f"`{source.SIDES_PATH}`. It is written by the comparison step of the portable "
            "benchmark."
        )
        lines.append("")
        return lines

    columns = record.get("columns", {})
    sides = record.get("sides", [])
    lines.append(
        "The portable benchmark runs on a machine and on a hosted notebook from the same source, "
        "so the question is not whether the two agree in spirit but whether they produce the "
        "same numbers. Each side writes its own runs; the comparison reads both and joins them "
        "on the task."
    )
    lines.append("")

    rows = [
        [
            name,
            columns[name].get("tasks", "-"),
            columns[name].get("passed", "-"),
            f"{source.number(columns[name].get('composite')):.2f}",
        ]
        for name in sorted(columns)
    ]
    lines.extend(table(["run", "tasks", "tasks passed", "composite"], rows))
    lines.append("")

    agreed, total = source.agreement(root)
    if total:
        if agreed == total:
            lines.append(
                f"The two sides agree on every one of the {total} shared rows, which is the "
                "finding: the arithmetic and the task definitions produce the same answer in "
                "two different environments, so a later difference between them is a difference "
                "in the model rather than in the harness."
            )
        else:
            lines.append(
                f"The two sides agree on {agreed} of {total} shared rows. A disagreement is a "
                "finding about the environment rather than about a model, and it is the reason "
                "the comparison is written down instead of being assumed."
            )
        lines.append("")

    if len(sides) > 1:
        lines.append(
            "These rows measure fixture solvers rather than a language model. A correct solver "
            "and a careless one are both deterministic, so the pair is the calibration of the "
            "suite: the gap between them is what the arithmetic can see, and the agreement "
            "across the two sides is what says the environment did not change the answer. A "
            "live model is another entry in `tools/kaggle/gembench/reference.py`, in the same "
            "shape, and it is measured by the same code."
        )
        lines.append("")
        lines.append(
            "A finished run also writes figures, a dataset of one row per solver and task, and a "
            "single archive of all of it. Those are read from the run directories under "
            "`results/benchmark/` and are the record of what was measured."
        )
        lines.append("")
    return lines


def run_section() -> list[str]:
    lines = ["## Running it", ""]
    lines.append(
        "The engine runs in a container and the harness drives it over its own HTTP endpoint."
    )
    lines.append("")
    lines.extend(
        [
            "```bash",
            "# one recorded run against the local engine",
            "php agent.php --run $RUN_ID --model gemma-4-E2B-it-Q4_K_M",
            "",
            "# the same tasks with the extended set",
            "php agent.php --run $RUN_ID --suite all",
            "",
            "# every local model in turn, then one comparison over all of them",
            "./benchmark-models.sh --suite all",
            "php benchmark.php --compare --dir results/benchmark",
            "",
            "# the portable benchmark, on this machine",
            "python3 tools/kaggle/gembench/runner.py --profile reference --suite all --check \\",
            "    --out results/benchmark/local",
            "python3 tools/kaggle/gembench/report.py --side local=results/benchmark/local \\",
            "    --side kaggle=results/benchmark/kaggle --out results/benchmark",
            "",
            "# the hosted notebook that runs the same source",
            "python3 tools/kaggle/kernel.py --owner $KAGGLE_OWNER --check",
            "",
            "# the pre-push check: install it once per clone, then it runs on its own",
            "python3 tools/security/scan_secrets.py --install-hook",
            "python3 tools/security/scan_secrets.py --tracked --paths",
            "",
            "# and the wide view, every blob any ref can reach, before or after a rewrite",
            "python3 tools/security/scan_secrets.py --history",
            "",
            "# machine paths in a record, shortened to repository relative form",
            "python3 tools/normalize_paths.py --check",
            "",
            "# and this document",
            "python3 tools/build_readme.py",
            "python3 tools/build_readme.py --check",
            "",
            "# sending to the private repository, with the review run first",
            "python3 tools/release/post_private.py --dry-run",
            "python3 tools/release/post_private.py",
            "",
            "# and to the copy that leaves the machine, with the same reading",
            "python3 tools/release/post_public.py --dry-run",
            "python3 tools/release/post_public.py",
            "```",
        ]
    )
    lines.append("")
    lines.append(
        "A checkout is sent as a reviewed commit rather than as a push, and the two destinations "
        "are two commands rather than one with a flag, because what they refuse is not the same. "
        "`tools/release/` reads the commit first: whether anything that belongs on this machine "
        "is in the tree, how large the largest file in it is, and what every file hashes to. "
        "Then `post_private.py` sends it to the private repository, and `post_public.py` sends "
        "it to the public copy, where the commit also has to be one the private remote already "
        "holds. Neither trusts a receipt written earlier: the reading is taken again, so a "
        "commit that moved between the two is read again instead of assumed still clean. "
        "Nothing is sent when the reading is not clean."
    )
    lines.append("")
    lines.append(
        "The harness never reaches the network on its own. A run that needs the hosted reference "
        "model reads its credential from the environment, which is the only place a credential is "
        "expected to be, and the scanner refuses a commit that puts one in a file instead."
    )
    lines.append("")
    lines.append(
        "The same check refuses two more things. It refuses a directory that exists only for "
        "local work, and it refuses a machine path, because a recorded run that carries the "
        "checkout location also carries the account name and whatever sits beside it. A path is "
        "written into a record in the form a reader elsewhere can use: relative to this "
        "repository, or under a home or temporary marker. `lib/PathRecord.php` is that rule for "
        "the writers, `tools/normalize_paths.py` applies the same rule to records written before "
        "it existed, and the check keeps it from coming back."
    )
    lines.append("")
    return lines


def licence_section(root: Path) -> list[str]:
    lines = ["## License", ""]
    record = source.licence(root)
    if not record["present"]:
        lines.append(
            "No license file was found in this checkout, so the terms the work is offered "
            "under could not be read."
        )
        lines.append("")
        return lines

    identifier = record["spdx"] or "an identifier the manifest does not declare"
    lines.append(
        f"The work is offered under {identifier}, and the file that carries the terms is "
        f"`{record['file']}`. The desktop application declares the same identifier in its own "
        "manifest, so a consumer of the source and a consumer of the binary are told the same "
        "thing by the file they are reading."
    )
    lines.append("")
    lines.extend(
        table(
            ["field", "value"],
            [
                ["identifier", identifier],
                ["holder", record["holder"]],
                ["license file", f"`{record['file']}`, {record['lines']} lines"],
                ["author in the desktop manifest", record["author"]],
            ],
        )
    )
    lines.append("")
    if record["agrees"]:
        lines.append(
            f"The copyright line the license file carries names {record['holder']}, which is "
            "the same holder the desktop manifest records as the author. "
            "`lib/DistributionCheck.php` reads both and fails the self-check when they stop "
            "agreeing, because a license naming one party while the package declares another "
            "is a license a consumer cannot act on."
        )
    else:
        lines.append(
            "The license file and the desktop manifest do not name the same holder, and the "
            "self-check reports that rather than choosing between them."
        )
    lines.append("")
    lines.append(
        "The identifier above is read from the manifest rather than kept in a list in this "
        "builder, so the license this project is under is stated once, in the file that ships "
        "with the application."
    )
    lines.append("")
    return lines


def distribution_section(root: Path) -> list[str]:
    lines = ["## Distribution", ""]
    record = source.distribution(root)
    if not record["app_id"]:
        lines.append(
            "The desktop manifest declares no build configuration, so there is nothing to "
            "report about how the application is packaged."
        )
        lines.append("")
        return lines

    lines.append(
        f"Everything else in this repository is source. `{record['product']}` is the one "
        "artefact that leaves as a binary, and a binary handed to somebody else is refused by "
        "their machine unless it was signed and notarized. Both are declared in "
        "`desktop/package.json` beside the application they belong to, so the version, the "
        "identifier and the way it is signed move in one commit."
    )
    lines.append("")
    entries = ", ".join(
        f"`{name}` ({record['entitlements_counts'].get(name, 0)} entries)"
        for name in record["entitlements"]
    )
    lines.extend(
        table(
            ["field", "value"],
            [
                ["identifier", record["app_id"]],
                ["product", f"{record['product']} {record['version']}"],
                ["hardened runtime", "on" if record["hardened"] else "off"],
                ["minimum system version", record["minimum_system"]],
                ["entitlements", entries],
                ["notarization hook", f"`{record['hook']}`" if record["hook"] else "none declared"],
                ["output", f"`desktop/{record['output']}/`, held out of version control"],
            ],
        )
    )
    lines.append("")
    lines.append(
        "The hardened runtime is what makes the entitlements apply at all: without it a signed "
        "bundle runs with the permissions of a debug build. The entries are the runtime's own "
        "requirements rather than a list grown until a build stopped complaining, and there "
        "are two files because a helper process does not inherit the entitlements of the "
        "application that started it. A helper without them is killed at load time on every "
        "machine except the one that built it, which is the failure that reaches a user and "
        "not a build log."
    )
    lines.append("")
    if record["hook_names"]:
        names = ", ".join(f"`{name}`" for name in record["hook_names"])
        switch = f"`{record['hook_switch']}`" if record["hook_switch"] else "the release switch"
        lines.append(
            f"The hook holds no credential. Every value it uses is read from the environment, "
            f"from {names}, so this checkout can be read by anybody and the secret stays in a "
            f"keychain or in the shell that started the build. Setting {switch} to `1` turns a "
            "missing credential from a line in the build log into a stopped build, which is "
            "what separates a local build from a release."
        )
        lines.append("")
    lines.extend(
        [
            "```bash",
            "cd desktop && npm install          # restore the pinned build tooling",
            "npm run dist:mac                   # sign, then notarize and staple through the hook",
            "npm run verify                     # read the signature back out of the bundle",
            "AGENT_LIKE_RELEASE=1 npm run release   # the same, and a missing credential stops it",
            "```",
        ]
    )
    lines.append("")
    verify = record["verify_script"].split()[-1] if record["verify_script"].split() else ""
    lines.append(
        f"The configuration states what a build was asked to do, so it is not evidence that a "
        f"build did it. `desktop/{verify}` reads four properties back from the finished bundle: "
        "whether the signature verifies, whether it was made under the hardened runtime with a "
        "Developer ID certificate rather than an ad hoc one, whether Gatekeeper accepts the "
        "bundle, and whether the notarization ticket is stapled to it. A bundle can pass the "
        "first three and fail the last, and that bundle opens on the machine that built it and "
        "nowhere else, which is why they are four findings and not one."
    )
    lines.append("")
    return lines


def exclusions_section() -> list[str]:
    return [
        "## What is not in this repository",
        "",
        "Model weights, the Electron runtime, derived caches, task scratch, unpublished research "
        "notes, captured vendor pricing, and build artefacts are held outside version control. "
        "Some of them are large, some are regenerable in one command, and some are work that is "
        "not ready to leave the machine. `.gitignore` carries the full list with the reason for "
        "each entry, because a rule whose reason is lost is a rule somebody deletes.",
        "",
        "The evidence a run leaves behind is the opposite case and is committed. The CSVs, the "
        "manifests, the figures and the dataset under `results/` are small, they are the record "
        "of what was measured, and a claim without one is not a result.",
        "",
        "One part of that evidence is held out: the interaction traces. Every probe the window "
        "makes leaves an `events.ndjson`, and the load captures are the scratch of a test whose "
        "result is kept separately. They are the run narrating itself rather than a measurement, "
        "and they carry the absolute paths, the loopback address and the temporary directory of "
        "the device that produced them. The traces stay on the machine that made them; the "
        "measured documents beside them are what travels.",
        "",
        "The transcript of a conversation is held out for the same reason. The interactive front "
        "end writes a markdown copy of each run into `model-tests/`, the directory the "
        "`transcript_dir` setting names beside the tester, and that directory is created on the "
        "first write rather than committed, so a checkout that has never run the tester does not "
        "have one and does not need one. A transcript records the prompts that were tried and the "
        "raw output that came back, which is the same kind of thing as a trace: a place a "
        "conversation was worked out, not a claim about a model.",
        "",
        "The last one is not evidence and not scratch. `Standard Operation Procedures/` holds the "
        "procedure a checkout passes before any of it is sent to either copy, and the two "
        "receipts that procedure writes, which name the commit, the time, the destination and "
        "every file that would be sent. It is held out because it describes how the work is run "
        "rather than what it found, and because a published procedure reads as a promise about "
        "the state of the public copy, which it is not. The directory is created on the first "
        "review rather than committed, so a checkout that has never sent anything does not have "
        "one either. What the procedure describes is a command, `python3 "
        "tools/release/post_public.py`, so the procedure and the check that runs it cannot come "
        "to describe different things.",
        "",
    ]


def footer() -> list[str]:
    return [
        "---",
        "",
        "Generated by `tools/build_readme.py` from `tools/readme_source.py`. Every number above "
        "is read from the artefact that owns it at build time; change a task, a weight file or a "
        "recorded run and run the builder again, because `--check` fails the build until the "
        "document matches.",
        "",
    ]


def build(root: Path, record: bool = True) -> str:
    models, origin = source.read_models(root, record=record)
    blocks: list[list[str]] = [
        ["# agent-like", ""],
        [
            "A harness for measuring what a small local language model can actually finish. It "
            "gives a model a fixed set of tools, a sandbox and a step budget, then checks the "
            "end state of the workspace rather than the model's account of it. The same tasks "
            "run on a machine and on a hosted notebook from one source, so a result carries the "
            "conditions that produced it and can be re-run anywhere.",
            "",
        ],
        layout_section(root),
        mechanism_section(),
        models_section(root, models, origin),
        suite_section(),
        scoring_section(),
        ["## Recorded results", ""],
        [
            "Every table below is read from the run directories under `results/`, by column "
            "name, so a run written by the local harness and a run written by the portable "
            "benchmark are read the same way.",
            "",
        ],
        agent_section(root),
        sides_section(root),
        run_section(),
        distribution_section(root),
        licence_section(root),
        exclusions_section(),
        footer(),
    ]

    text = "\n".join(line for block in blocks for line in block)
    return re.sub(r"\n{3,}", "\n\n", text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="build_readme",
        description="Write README.md from the artefacts that own its numbers.",
    )
    parser.add_argument("--root", default=str(PROJECT), help="the repository root")
    parser.add_argument("--out", default=README_NAME, help="the file to write, relative to the root")
    parser.add_argument("--check", action="store_true", help="fail when the file on disk is stale")
    parser.add_argument(
        "--no-record",
        action="store_true",
        help="do not refresh the recorded model reading",
    )
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    destination = root / args.out
    rendered = build(root, record=not args.no_record)

    if args.check:
        current = source.read_text(destination)
        if current == rendered:
            print(f"build_readme: {args.out} is up to date")
            return 0
        if _same_except_provenance(current, rendered):
            print(
                f"build_readme: {args.out} is up to date; the only difference is which machine "
                "the model reading came from"
            )
            return 0
        print(
            f"build_readme: {args.out} is out of date, run the builder without --check",
            file=sys.stderr,
        )
        _explain_difference(current, rendered)
        return 1

    destination.write_text(rendered, encoding="utf-8")
    print(f"build_readme: wrote {len(rendered.splitlines())} lines to {args.out}")
    return 0


#: The one sentence in the document that is allowed to differ between machines.
#: It names where the model reading came from, which is the weight directory on
#: the machine that holds it and the record on one that does not. A staleness
#: check that failed on a checkout for this reason would be a check people learn
#: to ignore, so it is compared with the sentence set aside. The match runs to
#: the start of the sentence that follows it, because the path a reading came
#: from can itself contain a full stop.
PROVENANCE = re.compile(r"This table was built from .*?\. Every cell comes from", re.DOTALL)
PROVENANCE_PLACEHOLDER = "This table was built from <the model reading>. Every cell comes from"


def _without_provenance(text: str) -> str:
    return PROVENANCE.sub(PROVENANCE_PLACEHOLDER, text)


def _same_except_provenance(current: str, rendered: str) -> bool:
    return _without_provenance(current) == _without_provenance(rendered)


def _explain_difference(current: str, rendered: str) -> None:
    """Point at the first line that differs, rather than printing two documents."""
    before_lines = current.splitlines()
    after_lines = rendered.splitlines()
    for index, (before, after) in enumerate(zip(before_lines, after_lines), start=1):
        if before != after:
            print(f"  first difference at line {index}", file=sys.stderr)
            print(f"    on disk: {before[:120]}", file=sys.stderr)
            print(f"    builder: {after[:120]}", file=sys.stderr)
            return
    print(
        f"  length differs: {len(before_lines)} lines on disk, "
        f"{len(after_lines)} from the builder",
        file=sys.stderr,
    )


if __name__ == "__main__":
    raise SystemExit(main())
