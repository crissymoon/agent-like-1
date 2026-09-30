"""Builds the hosted-benchmark kernel: the notebook, its metadata, and the checks.

The notebook is generated rather than hand written, and it is generated from the
modules that also run on a machine, so the two cannot disagree about what a task
is. This file is only the builder: it asks the notebook module for a source,
converts that source with the tooling that owns the notebook format, holds the
result to the format rules, writes the metadata the platform reads, and prints
the commands that publish it.

The tooling that owns the format is the house's Kaggle tool, not a second
implementation kept here. It is found by walking up from the project towards a
sibling that holds it, and it can be named outright, because a build that
guessed wrong would be a build whose lint did not run.

    python3 tools/kaggle/kernel.py --owner <kaggle-owner> --check
    python3 tools/kaggle/kernel.py --owner <kaggle-owner> --json results/benchmark/kernel-build.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent.parent

if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from gembench import notebook  # noqa: E402

#: The toolbelt module this builder is one end of. It owns the notebook format,
#: the metadata template, the linter and the CLI calls, so none of that is
#: restated here.
TOOLBELT_MODULE = "kaggle_kernel.py"

#: The toolbelt is a sibling project, so a command that names it by absolute
#: path records the machine rather than the build: the account name, the
#: directory the two projects sit in, and how they are laid out beside each
#: other. The token stands for wherever the directory is, which is the one thing
#: about it a reader with their own checkout needs.
TOOLS_TOKEN = "<tools>"

DEFAULT_SLUG = "agent-benchmark"
DEFAULT_TITLE = "Agent Benchmark"


def recorded_path(path: Path) -> str:
    """A path as the build record keeps it.

    Under the project it is relative to the project, which is what a reader with
    a checkout can follow. Outside it the path belongs to another project, and
    where that project sits is a fact about this machine, so the token is kept
    in its place.
    """
    resolved = path.expanduser().resolve()
    try:
        return str(resolved.relative_to(PROJECT.resolve()))
    except ValueError:
        return f"{TOOLS_TOKEN}/{resolved.name}"


def resolve_tools(explicit: str | None) -> Path:
    """The directory holding the house's Kaggle tool.

    Named outright, then taken from the environment, then looked for beside the
    project: the tool lives in a sibling house, and a build has to find it or
    say it could not.
    """
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    if os.environ.get("SQL_MGR_TOOLS"):
        candidates.append(Path(os.environ["SQL_MGR_TOOLS"]).expanduser())
    for parent in [PROJECT, *PROJECT.parents]:
        candidates.append(parent / "sql-mgr" / "tools")

    for candidate in candidates:
        if (candidate / TOOLBELT_MODULE).is_file():
            return candidate.resolve()

    raise SystemExit(
        "the house's Kaggle tool was not found; pass --tools <dir holding "
        + TOOLBELT_MODULE
        + "> or set SQL_MGR_TOOLS"
    )


def load_toolbelt(tools_dir: Path):
    """Import the toolbelt module, which also brings the linter with it."""
    if str(tools_dir) not in sys.path:
        sys.path.insert(0, str(tools_dir))
    import kaggle_kernel  # noqa: PLC0415

    return kaggle_kernel


def build(directory: Path, owner: str, slug: str, title: str, private: bool, tools: str | None = None) -> dict:
    """Write the notebook and the metadata, and return what was written."""
    tools_dir = resolve_tools(tools)
    toolbelt = load_toolbelt(tools_dir)

    directory.mkdir(parents=True, exist_ok=True)

    source = notebook.source()
    notebook_path = directory / "notebook.ipynb"
    toolbelt.write_notebook(source, notebook_path)

    document = toolbelt.notebook_from_source(source)
    identifier = f"{owner}/{slug}"
    body = toolbelt.kernel_metadata(
        identifier,
        title,
        code_file=notebook_path.name,
        competitions=(),
        datasets=(),
        gpu=False,
        private=private,
        internet=False,
    )
    metadata_path = toolbelt.write_json(body, directory / toolbelt.KERNEL_METADATA_FILENAME)

    kinds = [cell["cell_type"] for cell in document["cells"]]
    return {
        "directory": recorded_path(directory),
        "notebook": recorded_path(notebook_path),
        "metadata": recorded_path(metadata_path),
        "identifier": identifier,
        "cells": len(document["cells"]),
        "code_cells": kinds.count("code"),
        "markdown_cells": kinds.count("markdown"),
        "tools_module": TOOLBELT_MODULE,
        "tools_dir_live": str(tools_dir),
        "notebook_document": document,
        "toolbelt": toolbelt,
    }


def findings_for(built: dict) -> list[str]:
    """Everything that would make the kernel wrong on the platform."""
    found = list(notebook.module_findings())
    toolbelt = built["toolbelt"]

    for problem in toolbelt.validate_notebook_file(Path(built["notebook"])):
        found.append(f"notebook format: {problem}")
    for problem in toolbelt.notebook_findings(built["notebook_document"]):
        found.append(f"notebook: {problem}")

    body = json.loads(Path(built["metadata"]).read_text(encoding="utf-8"))
    if body.get("is_private") != "true":
        found.append("the kernel is not private, so a run would be published")
    if body.get("enable_internet") != "false":
        found.append("the kernel asks for internet, and a benchmark that could call out is not one")
    if body.get("enable_gpu") != "false":
        found.append("the kernel asks for an accelerator, which the benchmark does not use")
    if body.get("competition_sources"):
        found.append("the kernel names a competition, which this benchmark has nothing to do with")

    embedded = set(notebook.MODULE_ORDER)
    missing = [name for name in embedded if f"MODULES['{name}']" not in Path(built["notebook"]).read_text(encoding="utf-8")]
    if missing:
        found.append("the notebook does not carry module(s): " + ", ".join(sorted(missing)))

    return found


def commands(built: dict) -> list[str]:
    """The publish loop, in the order it is run, in the form a record keeps it."""
    directory = built["directory"]
    identifier = built["identifier"]
    tool = f"{TOOLS_TOKEN}/{TOOLBELT_MODULE}"
    return [
        f"python3 {tool} push --dir {directory}",
        f"python3 {tool} watch {identifier} --timeout 1800 --path results/benchmark/kaggle-pull",
        f"python3 {tool} verify-notebook {identifier} --dir {directory} "
        "--json results/benchmark/kernel-roundtrip.json",
    ]


def parser() -> argparse.ArgumentParser:
    body = argparse.ArgumentParser(
        prog="kernel.py",
        description="Build the hosted-benchmark kernel from the modules that run on a machine.",
    )
    body.add_argument("--dir", default=str(PROJECT / "kernels" / "gemma-benchmark"))
    body.add_argument("--owner", default=os.environ.get("KAGGLE_OWNER", ""), help="kaggle account the kernel is published under")
    body.add_argument("--slug", default=DEFAULT_SLUG)
    body.add_argument("--title", default=DEFAULT_TITLE)
    body.add_argument("--tools", help="directory holding the house's kaggle_kernel.py")
    body.add_argument("--public", action="store_true", help="publish the notebook (default: private)")
    body.add_argument("--check", action="store_true", help="exit non-zero on anything wrong with the kernel")
    body.add_argument("--json", default="", help="write the build record here")
    return body


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)

    if not args.owner:
        print("--owner is required, or set KAGGLE_OWNER", file=sys.stderr)
        return 2

    built = build(
        Path(args.dir), args.owner, args.slug, args.title, private=not args.public, tools=args.tools
    )

    print(f"notebook : {built['notebook']}")
    print(f"metadata : {built['metadata']}")
    print(f"identifier: {built['identifier']}")
    print(
        f"cells    : {built['cells']} "
        f"({built['code_cells']} code, {built['markdown_cells']} markdown)"
    )
    print(f"toolbelt : {built['tools_dir_live']}")

    found = findings_for(built)
    for item in found:
        print(f"[error] {item}")
    if not found:
        print("check    : the notebook is well formed and the metadata asks for a private cpu run")

    print()
    print("to publish:")
    for line in commands(built):
        print("  " + line)

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        # The module objects and the live toolbelt path stay out of the record:
        # the first two are processes rather than facts, and the third is where
        # this machine happens to keep a sibling project.
        record = {
            key: value
            for key, value in built.items()
            if key not in {"notebook_document", "toolbelt", "tools_dir_live"}
        }
        record["tools_note"] = (
            "the toolbelt that owns the notebook format is a sibling project, so its "
            f"directory is recorded as the token {TOOLS_TOKEN} rather than as the path "
            "it had on the machine that ran this build"
        )
        record["findings"] = found
        record["commands"] = commands(built)
        out.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {out}")

    if args.check and found:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
