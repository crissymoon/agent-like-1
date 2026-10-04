"""
Sandboxed testing.

Builds a throwaway copy of the pieces the change touches, overlays the proposed
shadow files, and runs the requested command inside it. The live tree is never
the working directory of a model-suggested command.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from config import IGNORE_DIRS, TESTS_DIR, TARGET_ROOT
from dashboard.jobs import Job
from security import guarded_run

_COPY_BUDGET_BYTES = 200 * 1024 * 1024


def _top_levels(files: list[str]) -> set[str]:
    tops = set()
    for rel in files:
        parts = Path(rel).parts
        if parts:
            tops.add(parts[0])
    return tops


def build_env(job: Job, root: Path | None = None) -> Path:
    """Copy the relevant top-level entries into a per-job sandbox."""
    base = root or TARGET_ROOT
    sandbox = TESTS_DIR / job.id
    if sandbox.exists():
        shutil.rmtree(sandbox)
    sandbox.mkdir(parents=True, exist_ok=True)

    copied = 0
    for top in sorted(_top_levels(job.files)):
        source = base / top
        if not source.exists():
            continue
        if source.is_dir():
            if top in IGNORE_DIRS:
                continue
            destination = sandbox / top
            shutil.copytree(
                source, destination,
                ignore=shutil.ignore_patterns(*IGNORE_DIRS, "*.log", "*.gguf"),
                dirs_exist_ok=True,
            )
            copied += sum(f.stat().st_size for f in destination.rglob("*") if f.is_file())
        else:
            if source.stat().st_size <= _COPY_BUDGET_BYTES:
                shutil.copy2(source, sandbox / top)
                copied += source.stat().st_size
        if copied > _COPY_BUDGET_BYTES:
            raise RuntimeError("sandbox copy exceeded the size budget; narrow the file list")

    # Overlay the proposed shadow versions.
    for edit in job.edits:
        shadow = Path(edit.get("shadow_path", ""))
        if shadow.exists():
            destination = sandbox / edit["filepath"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(shadow, destination)

    return sandbox


def _default_command(files: list[str]) -> str:
    py_files = [f for f in files if f.endswith(".py")]
    if py_files:
        return "python3 -m py_compile " + " ".join(py_files)
    php_files = [f for f in files if f.endswith(".php")]
    if php_files:
        return "php -l " + " ".join(php_files)
    return ""


def test_job(job: Job, command: str = "", root: Path | None = None) -> dict:
    """Run a command against the overlaid sandbox and return the captured result."""
    try:
        sandbox = build_env(job, root)
    except Exception as exc:  # noqa: BLE001 - reported to the reviewer
        return {"allowed": True, "success": False, "error": f"could not build sandbox: {exc}"}

    resolved = command.strip() or job.run_command.strip() or _default_command(job.files)
    if not resolved:
        return {"allowed": True, "success": False, "error": "no test command provided"}

    result = guarded_run.run(resolved, cwd=sandbox)
    result["sandbox"] = str(sandbox)
    return result
