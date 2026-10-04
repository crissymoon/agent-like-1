"""
Orchestrator.

The seam between the guarded runner and the rest of the system. It normalises a
request (repairing the model's shape drift), builds the context that fits the
model's window, stages edits into the shadow copy, lints what it staged, and
records everything as one reviewable job. It is the only module that both the
runner and the dashboard worker depend on.

Two heavier helpers sit beside the direct path. A file too large for one prompt
is rewritten a chunk at a time and merged, so the model never has to hold the
whole file. And a staged file with lint defects can be repaired surgically from
the defect list instead of being rewritten.
"""
from __future__ import annotations

from pathlib import Path

from config import (
    CHUNK_MAX_CHARS,
    CONTEXT_CHAR_BUDGET,
    DASHBOARD_URL,
    LINT_ON_STAGE,
    ORIGINAL_COPY,
    REPAIR_MAX_ATTEMPTS,
    SHADOW_COPY,
    TARGET_ROOT,
)
from core import adapter, chunk_merge, linters, surgical_patcher
from dashboard.jobs import Job, JobStore
from engine.model_client import ModelClient, ModelError
from engine.prompt_repair import normalize_tool_arguments
from engine.events import NULL_REPORTER, Reporter
from security import guarded_run


def job_url(job_id: str) -> str:
    return f"{DASHBOARD_URL}/job/{job_id}"


def normalize_request(request: dict) -> dict:
    """Coerce an inbound request into the shape the pipeline expects."""
    return normalize_tool_arguments(request)


def _validate_files(files: list[str], root: Path) -> None:
    for rel in files:
        adapter.resolve(rel, root)  # raises AdapterError when it escapes the root


def _new_file_marker(rel: str, base: Path, omitted: list[str]) -> str | None:
    """
    Represent a referenced path that does not exist yet.

    A path that resolves inside the root but is absent is a file the author is
    being asked to create, so it is surfaced as ``status="new"`` rather than
    dropped. Anything that escapes the root or exists as a directory is omitted.
    """
    try:
        target = adapter.resolve(rel, base)
    except adapter.AdapterError:
        omitted.append(rel)
        return None
    if target.exists():
        omitted.append(rel)
        return None
    return f'<file path="{rel}" status="new"/>'


def build_prompt_context(
    files: list[str],
    note: str = "",
    base_texts: dict[str, str] | None = None,
    budget: int = CONTEXT_CHAR_BUDGET,
    root: Path | None = None,
) -> tuple[str, list[str], list[str]]:
    """
    Assemble prompt context for the given files, chunking anything large with
    overlap. ``base_texts`` lets a rework feed the previously proposed shadow
    instead of the pristine file, and files that do not exist yet are marked so
    the author can create them.
    """
    base = root or TARGET_ROOT
    base_texts = base_texts or {}
    used = 0
    blocks: list[str] = []
    included: list[str] = []
    omitted: list[str] = []

    for rel in files:
        if rel in base_texts:
            content = base_texts[rel]
            total_lines = content.count("\n") + 1
            if len(content) <= CHUNK_MAX_CHARS:
                segments = [f'<file path="{rel}" lines="{total_lines}">\n{content}\n</file>']
            else:
                segments = [
                    f'<file path="{rel}" lines="{total_lines}" chunk="{chunk.label}">\n{chunk.text}\n</file>'
                    for chunk in chunk_merge.plan(rel, root=base, text=content).chunks
                ]
        else:
            try:
                read = adapter.read_text(rel, root=base)
            except adapter.AdapterError:
                marker = _new_file_marker(rel, base, omitted)
                if marker is None:
                    continue
                segments = [marker]
            else:
                if len(read.content) <= CHUNK_MAX_CHARS:
                    segments = [f'<file path="{rel}" lines="{read.total_lines}">\n{read.content}\n</file>']
                else:
                    segments = [
                        f'<file path="{rel}" lines="{read.total_lines}" chunk="{chunk.label}">\n{chunk.text}\n</file>'
                        for chunk in chunk_merge.plan(rel, root=base, text=read.content).chunks
                    ]

        for segment in segments:
            if used + len(segment) > budget:
                omitted.append(rel)
                break
            blocks.append(segment)
            used += len(segment)
            if rel not in included:
                included.append(rel)

    header = f"<reviewer_note>{note}</reviewer_note>\n\n" if note else ""
    return header + "\n".join(blocks), included, omitted


def _lint_staged(staged: list[dict], reporter: Reporter) -> None:
    """Attach lint diagnostics to each staged edit, in place."""
    for edit in staged:
        shadow = Path(edit.get("shadow_path", ""))
        if not shadow.exists():
            edit["diagnostics"] = []
            continue
        try:
            found = linters.lint_shadow(shadow, edit["filepath"])
        except Exception:  # noqa: BLE001 - a broken linter must not fail the stage
            found = []
        edit["diagnostics"] = [item.to_dict() for item in found]
        if found:
            reporter.lint(edit["filepath"], edit["diagnostics"])


def stage_edits(
    edits: list[dict],
    base_texts: dict[str, str] | None = None,
    root: Path | None = None,
    stamp: str | None = None,
    reporter: Reporter = NULL_REPORTER,
    lint: bool = LINT_ON_STAGE,
) -> tuple[list[dict], list[str]]:
    """
    Apply each edit into the shadow copy.

    Returns (staged_edits, errors). A single stamp is shared so all edits from
    one request land in the same timestamped directory.
    """
    base = root or TARGET_ROOT
    stamp = stamp or surgical_patcher.timestamp_label()
    base_texts = base_texts or {}
    staged: list[dict] = []
    errors: list[str] = []

    for edit in edits:
        try:
            result = surgical_patcher.stage(
                edit["filepath"],
                base,
                edit["old_str"],
                edit["new_str"],
                regex=edit.get("regex", False),
                shadow_root=SHADOW_COPY,
                original_dir=ORIGINAL_COPY,
                stamp=stamp,
                base_text=base_texts.get(edit["filepath"]),
            )
            staged.append(result)
            reporter.stage(result["filepath"], result["match"], result["diff"].count("\n"))
        except surgical_patcher.PatchError as exc:
            errors.append(f"{edit['filepath']}: {exc}")
        except Exception as exc:  # noqa: BLE001 - surfaced to the reviewer
            errors.append(f"{edit['filepath']}: {exc}")

    if lint and staged:
        _lint_staged(staged, reporter)
    return staged, errors


def _repair_staged(
    client: ModelClient,
    instruction: str,
    staged: list[dict],
    base_texts: dict[str, str],
    root: Path,
    reporter: Reporter,
) -> None:
    """
    Try to clear lint defects with the smallest edit, in place.

    A repair is authored against the defect list and the current proposed
    content, then restaged on top of the same stamp. Attempts are bounded so a
    stubborn defect cannot become an endless loop.
    """
    for edit in staged:
        diagnostics = edit.get("diagnostics") or []
        if not diagnostics:
            continue
        current = edit.get("new_content", "")
        for attempt in range(REPAIR_MAX_ATTEMPTS):
            reporter.phase("repairing", f"{edit['filepath']} (attempt {attempt + 1})")
            block = f'<file path="{edit["filepath"]}">\n{current}\n</file>'
            try:
                repairs = client.generate_repairs(instruction, block, linters.format_diagnostics(
                    [linters.Diagnostic(**item) for item in diagnostics]
                ))
            except ModelError as exc:
                reporter.error(f"repair failed: {exc}")
                break
            if not repairs:
                break
            repair = repairs[0]
            try:
                applied = surgical_patcher.apply_edit(
                    current, repair["old_str"], repair["new_str"], regex=repair.get("regex", False)
                )
            except surgical_patcher.PatchError:
                break
            current = applied.new_content
            restaged, _ = stage_edits(
                [{"filepath": edit["filepath"], "old_str": "", "new_str": current, "regex": False}],
                base_texts={edit["filepath"]: edit["new_content"]},
                root=root, stamp=edit.get("shadow_stamp"), reporter=reporter,
            )
            if not restaged:
                break
            staged[staged.index(edit)] = restaged[0]
            base_texts[edit["filepath"]] = restaged[0]["new_content"]
            if not restaged[0].get("diagnostics"):
                break
            edit = restaged[0]
            diagnostics = restaged[0]["diagnostics"]
            current = restaged[0]["new_content"]


def rewrite_chunked(
    client: ModelClient,
    rel_path: str,
    root: Path,
    instruction: str,
    note: str = "",
    reporter: Reporter = NULL_REPORTER,
) -> dict | None:
    """
    Rewrite a large file a chunk at a time, then merge the windows.

    Each window is edited on its own so the model never holds the whole file,
    the overlap is trimmed at merge time, and a line two windows both changed is
    reported rather than silently overwritten. Returns one stage-able edit.
    """
    original = adapter.read_for_edit(rel_path, root)
    plan = chunk_merge.plan(rel_path, root=root, text=original)
    if not plan.chunked:
        return None

    edited: list[tuple] = []
    for chunk in plan.chunks:
        reporter.phase("chunk", f"{rel_path} {chunk.label}")
        block = f'<file path="{rel_path}" lines="{plan.total_lines}" chunk="{chunk.label}">\n{chunk.text}\n</file>'
        try:
            edits = client.generate_edits(instruction, block, note)
        except ModelError:
            edits = []
        text = chunk.text
        if edits:
            try:
                text = surgical_patcher.apply_edit(
                    chunk.text, edits[0]["old_str"], edits[0]["new_str"], regex=edits[0].get("regex", False)
                ).new_content
            except surgical_patcher.PatchError:
                text = edits[0]["new_str"] if not edits[0]["old_str"] else chunk.text
        edited.append((chunk, text))

    merged, _diff, conflicts = chunk_merge.merge_edit_results(original, edited)
    for conflict in conflicts:
        reporter.error(f"merge conflict: {conflict}")
    return {"filepath": rel_path, "old_str": original, "new_str": merged, "regex": False}


def handle_request(
    request: dict,
    root: Path | None = None,
    store: JobStore | None = None,
    reporter: Reporter = NULL_REPORTER,
) -> dict:
    """
    Entry point for the guarded runner. Validates, then either stages edits
    immediately or queues the job for the worker to generate them.
    """
    base = root or TARGET_ROOT
    store = store or JobStore()
    req = normalize_request(request)

    if not req["instruction"]:
        return {"ok": False, "error": "instruction is required"}

    try:
        _validate_files(req["files"], base)
        for edit in req["edits"]:
            adapter.resolve(edit["filepath"], base)
    except adapter.AdapterError as exc:
        return {"ok": False, "error": str(exc)}

    run_result = ""
    if req["run"] and req["execute"]:
        result = guarded_run.run(req["run"], cwd=base)
        if not result.get("allowed"):
            return {"ok": False, "error": result.get("error", "command rejected")}
        run_result = str(result)

    if req["edits"]:
        stamp = surgical_patcher.timestamp_label()
        staged, errors = stage_edits(req["edits"], root=base, stamp=stamp, reporter=reporter)
        if not staged:
            job = store.create(
                status="error", mode="direct", origin=req["origin"],
                instruction=req["instruction"], files=req["files"],
                run_command=req["run"], run_result=run_result,
                error="; ".join(errors) or "no edits staged", stamp=stamp,
            )
            return {"ok": False, "error": job.error, "job": job.to_dict()}
        job = store.create(
            status="pending_review", mode="direct", origin=req["origin"],
            instruction=req["instruction"], files=req["files"],
            edits=staged, run_command=req["run"], run_result=run_result,
            error="; ".join(errors), stamp=stamp,
        )
        return {"ok": True, "queued": False, "job": job.to_dict(), "url": job_url(job.id)}

    job = store.create(
        status="queued", mode=req["mode"], origin=req["origin"],
        instruction=req["instruction"], files=req["files"],
        run_command=req["run"], run_result=run_result,
        reviewer_note=req["reviewer_note"], parent_id=req["parent_id"],
    )
    return {"ok": True, "queued": True, "job": job.to_dict(), "url": job_url(job.id),
            "note": "queued for generation by the dashboard worker"}


def _parent_texts(job: Job, store: JobStore) -> dict[str, str]:
    if not job.parent_id:
        return {}
    parent = store.get(job.parent_id)
    if not parent:
        return {}
    return {edit["filepath"]: edit.get("new_content", "") for edit in parent.edits}


def process_job(job: Job, store: JobStore, client: ModelClient | None = None,
                root: Path | None = None, reporter: Reporter = NULL_REPORTER) -> Job:
    """
    Generate edits for a queued job and stage them. Used by the worker.

    Two passes produce the staged set. The first asks the model for targeted
    edits and discards any that are larger than a single prompt window. The
    second takes every referenced file that is too large for one prompt and still
    has no staged edit, and rewrites it a window at a time, merging the windows
    and reporting any seam two windows both touched. That is what keeps a file
    larger than the context window editable without ever holding the whole file
    in one prompt, and it is why a large file the model declined to match does
    not end the job as an error.
    """
    base = root or TARGET_ROOT
    client = client or ModelClient()
    chunked = False
    try:
        base_texts = _parent_texts(job, store)
        reporter.phase("thinking", job.instruction[:80])
        context, _included, _omitted = build_prompt_context(
            job.files, note=job.reviewer_note, base_texts=base_texts, root=base,
        )

        edits = client.generate_edits(job.instruction, context, job.reviewer_note)
        oversized = [edit for edit in edits if _oversized(edit)]
        if oversized:
            reporter.phase("oversized", f"{len(oversized)} edit(s) beyond one prompt window")
            edits = [edit for edit in edits if not _oversized(edit)]

        stamp = surgical_patcher.timestamp_label()
        staged, errors = stage_edits(
            edits, base_texts=base_texts, root=base, stamp=stamp, reporter=reporter,
        )

        targets = _chunk_targets(job, staged, base)
        if targets:
            reporter.phase("chunking", ", ".join(targets))
            chunked_edits = _rewrite_targets(
                client, targets, base, job.instruction, job.reviewer_note, reporter,
            )
            if chunked_edits:
                more, chunk_errors = stage_edits(
                    chunked_edits, base_texts=base_texts, root=base, stamp=stamp,
                    reporter=reporter,
                )
                staged.extend(more)
                errors.extend(chunk_errors)
                chunked = bool(more)

        if not staged:
            raise ModelError("; ".join(errors) or "model produced no usable edits")

        if any(edit.get("diagnostics") for edit in staged):
            _repair_staged(client, job.instruction, staged, base_texts, base, reporter)

        store.update(
            job.id, edits=staged, status="pending_review", stamp=stamp,
            error="; ".join(errors), chunked=chunked,
        )
    except Exception as exc:  # noqa: BLE001 - captured on the job for the reviewer
        store.update(job.id, status="error", error=str(exc))
    finally:
        store.release(job.id)
    return store.get(job.id) or job


def _oversized(edit: dict) -> bool:
    """An authored edit larger than one prompt window cannot be trusted whole."""
    return len(edit.get("new_str") or "") > CHUNK_MAX_CHARS


def _chunk_targets(job: Job, staged: list[dict], root: Path) -> list[str]:
    """
    Referenced files too large for one prompt that still have no staged edit.

    A large file is either edited directly, by a small exact match inside it, or
    it is rewritten a window at a time. When neither happened - the model
    returned nothing, returned an edit beyond one prompt window, or matched text
    that is not in the file - the file is a chunking target. Anything that
    already staged an edit is left alone so a targeted change is never widened.
    """
    done = {edit["filepath"] for edit in staged}
    targets: list[str] = []
    for rel in job.files:
        if rel in done:
            continue
        try:
            path = adapter.resolve(rel, root)
        except adapter.AdapterError:
            continue
        if not path.exists() or not path.is_file():
            continue
        if path.stat().st_size <= CHUNK_MAX_CHARS:
            continue
        targets.append(rel)
    return targets


def _rewrite_targets(client: ModelClient, rel_paths: list[str], root: Path,
                     instruction: str, note: str, reporter: Reporter) -> list[dict]:
    """Rewrite each named target a window at a time."""
    produced: list[dict] = []
    for rel in rel_paths:
        edit = rewrite_chunked(client, rel, root, instruction, note, reporter)
        if edit:
            produced.append(edit)
    return produced


def request_rework(job_id: str, note: str, store: JobStore | None = None) -> Job | None:
    """Queue a revision of a reviewed job, carrying the reviewer's note."""
    store = store or JobStore()
    parent = store.get(job_id)
    if parent is None:
        return None
    return store.create(
        status="queued", mode="rework", origin="dashboard",
        instruction=parent.instruction, files=parent.files,
        reviewer_note=note, parent_id=parent.id, run_command=parent.run_command,
    )


def tool_result(result: dict) -> dict:
    """Shape a handle_request result for return to the model as a tool message."""
    if not result.get("ok"):
        return {"success": False, "error": result.get("error", "request failed")}
    job = result["job"]
    edits = []
    for edit in job.get("edits", []):
        entry = {
            "filepath": edit["filepath"],
            "match": edit["match"],
            "diff_lines": edit["diff"].count("\n"),
        }
        diagnostics = edit.get("diagnostics") or []
        if diagnostics:
            entry["lint"] = linters.format_diagnostics(
                [linters.Diagnostic(**item) for item in diagnostics], limit=8
            )
        edits.append(entry)
    return {
        "success": True,
        "job_id": job["id"],
        "status": job["status"],
        "review_url": result["url"],
        "edits": edits,
    }
