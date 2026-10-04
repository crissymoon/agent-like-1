"""
The dashboard.

A small Flask app that owns the review surface: it lists jobs, renders each
diff with CodeMirror merge, and offers approve, reject, rework, and test. It
also runs the single background worker that generates edits for queued jobs.
"""
from __future__ import annotations

import json
import secrets
import shutil
from pathlib import Path

from flask import (
    Flask,
    Response,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from config import ORIGINAL_COPY, PALETTE, TARGET_ROOT
from dashboard import testing
from dashboard.jobs import JobStore
from dashboard.worker import start_worker
from engine import orchestrator

_store = JobStore()
_worker = None

#: The policy every dashboard page is served under, and the one its meta tag
#: repeats so the page declares it too. The nonce is issued per request and
#: named on each inline script, which is why the script-src directive does not
#: carry an inline allowance: the page answers for the script it wrote.
_CSP_TEMPLATE = (
    "default-src 'none'; "
    "script-src 'self' https://esm.sh 'nonce-{nonce}'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "form-action 'self'; "
    "base-uri 'none'; "
    "frame-ancestors 'none'"
)


def _edit_payload(job) -> list[dict]:
    """Original and proposed text for each edit, ready for CodeMirror merge."""
    payload = []
    for edit in job.edits:
        original_path = ORIGINAL_COPY / edit["filepath"]
        shadow_path = Path(edit.get("shadow_path", ""))
        original = original_path.read_text(encoding="utf-8", errors="replace") if original_path.exists() else ""
        modified = shadow_path.read_text(encoding="utf-8", errors="replace") if shadow_path.exists() else ""
        payload.append({
            "filepath": edit["filepath"],
            "original": original,
            "modified": modified,
            "diff": edit.get("diff", ""),
            "match": edit.get("match", ""),
            "start_line": edit.get("start_line", 0),
            "lines_removed": edit.get("lines_removed", 0),
            "lines_added": edit.get("lines_added", 0),
        })
    return payload


def create_app(start_background: bool = True) -> Flask:
    global _worker

    app = Flask(__name__, template_folder="templates", static_folder="static")

    if start_background and (_worker is None or not _worker.is_alive()):
        _worker = start_worker(_store)

    @app.before_request
    def _issue_nonce() -> None:
        """One nonce per request, so an inline script is named rather than trusted."""
        g.csp_nonce = secrets.token_urlsafe(16)

    @app.context_processor
    def _policy_context() -> dict:
        nonce = getattr(g, "csp_nonce", "")
        return {"csp_nonce": nonce, "csp_policy": _CSP_TEMPLATE.format(nonce=nonce)}

    @app.after_request
    def _declare_policy(response: Response) -> Response:
        response.headers["Content-Security-Policy"] = _CSP_TEMPLATE.format(
            nonce=getattr(g, "csp_nonce", "")
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    def _queue_state() -> dict:
        jobs = _store.list()
        return {
            "queued": sum(1 for j in jobs if j.status == "queued"),
            "processing": sum(1 for j in jobs if j.status == "processing"),
            "pending": sum(1 for j in jobs if j.status == "pending_review"),
            "busy": bool(_worker and _worker.busy),
            "current": _worker.current_job_id if _worker else "",
        }

    @app.get("/")
    def index() -> str:
        return render_template(
            "index.html",
            jobs=[j.to_dict() for j in _store.list()],
            queue=_queue_state(),
            palette=PALETTE,
            dashboard_url=request.host_url.rstrip("/"),
        )

    @app.get("/job/<job_id>")
    def job_view(job_id: str) -> Response | str:
        job = _store.get(job_id)
        if job is None:
            return Response("job not found", status=404)
        return render_template(
            "job.html",
            job=job.to_dict(),
            edits=_edit_payload(job),
            edits_json=json.dumps(_edit_payload(job)),
            queue=_queue_state(),
            palette=PALETTE,
        )

    @app.post("/job/<job_id>/approve")
    def approve(job_id: str) -> Response:
        job = _store.get(job_id)
        if job is None:
            return Response("job not found", status=404)
        if not job.edits:
            return Response("nothing to approve: no edits staged", status=400)
        applied = []
        for edit in job.edits:
            shadow = Path(edit.get("shadow_path", ""))
            if not shadow.exists():
                continue
            destination = TARGET_ROOT / edit["filepath"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(shadow, destination)
            applied.append(edit["filepath"])
        if not applied:
            return Response("no shadow files were found to apply", status=409)
        _store.update(job_id, status="approved")
        return redirect(url_for("job_view", job_id=job_id))

    @app.post("/job/<job_id>/reject")
    def reject(job_id: str) -> Response:
        _store.update(job_id, status="rejected")
        return redirect(url_for("job_view", job_id=job_id))

    @app.post("/job/<job_id>/rework")
    def rework(job_id: str) -> Response:
        note = request.form.get("note", "").strip()
        child = orchestrator.request_rework(job_id, note, _store)
        if child is None:
            return Response("job not found", status=404)
        return redirect(url_for("job_view", job_id=child.id))

    @app.post("/job/<job_id>/test")
    def test(job_id: str) -> Response:
        job = _store.get(job_id)
        if job is None:
            return Response("job not found", status=404)
        command = request.form.get("command", "").strip()
        _store.update(job_id, status="testing")
        result = testing.test_job(job, command)
        _store.update(job_id, status="pending_review", run_command=command or job.run_command,
                      run_result=json.dumps(result, indent=2))
        return redirect(url_for("job_view", job_id=job_id))

    @app.post("/api/submit")
    def api_submit() -> Response:
        payload = request.get_json(silent=True) or {}
        payload.setdefault("origin", "dashboard")
        result = orchestrator.handle_request(payload, store=_store)
        return jsonify(result), (200 if result.get("ok") else 400)

    @app.get("/api/jobs")
    def api_jobs() -> Response:
        return jsonify({"jobs": [j.to_dict() for j in _store.list()], "queue": _queue_state()})

    @app.get("/api/job/<job_id>")
    def api_job(job_id: str) -> Response:
        job = _store.get(job_id)
        if job is None:
            return jsonify({"error": "not found"}), 404
        return jsonify(job.to_dict())

    @app.get("/health")
    def health() -> Response:
        return jsonify({"ok": True, "queue": _queue_state()})

    return app


def main() -> None:
    from config import DASHBOARD_HOST, DASHBOARD_PORT

    create_app().run(host=DASHBOARD_HOST, port=DASHBOARD_PORT, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
