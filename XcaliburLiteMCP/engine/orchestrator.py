"""
Orchestrator.

The seam between the guarded runner and the rest of the system. It normalises a
request, builds the context that fits the model's window, stages edits into the
shadow copy, and records everything as a reviewable job. It is the only module
that both the runner and the dashboard worker depend on.
"""
from __future__ import annotations

from pathlib import Path

from config import CHUNK_MAX_CHARS, CONTEXT_CHAR_BUDGET, DASHBOARD_URL, ORIGINAL_COPY, SHADOW_COPY, TARGET_ROOT
from core import adapter, file_chunker, surgical_patcher
from dashboard.jobs import Job, JobStore
from engine.model_client import ModelClient, ModelError
from security import guarded_run


def job_url(job_id: str) -> str:
    return f"{DASHBOARD_URL}/job/{job_id}"


def normalize_request(request: dict) -> dict:
    """Coerce an inbound request into the shape the pipeline expects."""
    edits = request.get("edits") or []
    clean_edits = []
    for edit in edits:
        if not isinstance(edit, dict):
            continue
        if not edit.get("filepath") or edit.get("new_str") is None:
            continue
        clean_edits.append({
            "filepath": str(edit["filepath"]).strip(),
            "old_str": "" if edit.get("old_str") is None else edit["old_str"],
            "new_str": edit["new_str"],
            "regex": bool(edit.get("regex", False)),
        })
    files = [str(f).strip() for f in (request.get("files") or []) if str(f).strip()]
    if not files and clean_edits:
        files = [edit["filepath"] for edit in clean_edits]
    return {
        "instruction": str(request.get("instruction", "")).strip(),
        "files": files,
        "edits": clean_edits,
        "run": str(request.get("run", "")).strip(),
        "execute": bool(request.get("execute", False)),
        "origin": str(request.get("origin", "agent")),
        "mode": str(request.get("mode", "direct" if clean_edits else "auto")),
        "reviewer_note": str(request.get("reviewer_note", "")).strip(),
        "parent_id": str(request.get("parent_id", "")).strip(),
    }


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
        segments: list[str] = []
        if rel in base_texts:
            content = base_texts[rel]
            total_lines = content.count("\n") + 1
            if len(content) <= CHUNK_MAX_CHARS:
                segments = [f'<file path="{rel}" lines="{total_lines}">\n{content}\n</file>']
            else:
                segments = [
                    f'<file path="{rel}" lines="{total_lines}" chunk="{chunk.label}">\n{chunk.text}\n</file>'
                    for chunk in file_chunker.chunk_lines(content)
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
                        for chunk in file_chunker.chunk_lines(read.content)
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


def stage_edits(
    edits: list[dict],
    base_texts: dict[str, str] | None = None,
    root: Path | None = None,
    stamp: str | None = None,
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
            result["instruction_ref"] = edit.get("filepath")
            staged.append(result)
        except surgical_patcher.PatchError as exc:
            errors.append(f"{edit['filepath']}: {exc}")
        except Exception as exc:  # noqa: BLE001 - surfaced to the reviewer
            errors.append(f"{edit['filepath']}: {exc}")

    return staged, errors


def handle_request(request: dict, root: Path | None = None, store: JobStore | None = None) -> dict:
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
        staged, errors = stage_edits(req["edits"], root=base, stamp=stamp)
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


def process_job(job: Job, store: JobStore, client: ModelClient | None = None,
                root: Path | None = None) -> Job:
    """Generate edits for a queued job and stage them. Used by the worker."""
    base = root or TARGET_ROOT
    client = client or ModelClient()
    try:
        base_texts: dict[str, str] = {}
        if job.parent_id:
            parent = store.get(job.parent_id)
            if parent:
                base_texts = {
                    edit["filepath"]: edit.get("new_content", "")
                    for edit in parent.edits
                }

        context, _included, _omitted = build_prompt_context(
            job.files, note=job.reviewer_note, base_texts=base_texts, root=base,
        )
        edits = client.generate_edits(job.instruction, context, job.reviewer_note)

        stamp = surgical_patcher.timestamp_label()
        staged, errors = stage_edits(edits, base_texts=base_texts, root=base, stamp=stamp)
        if not staged:
            raise ModelError("; ".join(errors) or "model produced no usable edits")

        store.update(
            job.id, edits=staged, status="pending_review", stamp=stamp,
            error="; ".join(errors),
        )
    except Exception as exc:  # noqa: BLE001 - captured on the job for the reviewer
        store.update(job.id, status="error", error=str(exc))
    finally:
        store.release(job.id)
    return store.get(job.id) or job


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
    return {
        "success": True,
        "job_id": job["id"],
        "status": job["status"],
        "review_url": result["url"],
        "edits": [
            {"filepath": edit["filepath"], "match": edit["match"], "diff_lines": edit["diff"].count("\n")}
            for edit in job.get("edits", [])
        ],
    }
