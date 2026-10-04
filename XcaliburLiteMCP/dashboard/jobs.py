"""
Job store and queue.

Jobs are JSON files under workspace/jobs. Writes are atomic so the dashboard
and the runner can share the directory across processes. A single-file claim
lock guarantees exactly one worker processes a queued job.
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from config import JOBS_DIR

# Lifecycle:
#   queued      -> waiting for the worker to generate edits
#   processing  -> the worker holds the claim
#   pending_review -> edits staged, human decides in the dashboard
#   testing     -> a temp environment is running the change
#   approved / rejected / error -> terminal for this job
STATUSES = ("queued", "processing", "pending_review", "testing", "approved", "rejected", "error")


@dataclass
class Job:
    id: str
    created: str
    status: str = "pending_review"
    mode: str = "direct"                 # direct | auto | rework
    origin: str = "agent"                # agent | dashboard | cli
    instruction: str = ""
    files: list[str] = field(default_factory=list)
    run_command: str = ""
    run_result: str = ""
    edits: list[dict] = field(default_factory=list)
    reviewer_note: str = ""
    parent_id: str = ""
    error: str = ""
    stamp: str = ""
    # True when a referenced file was too large for one prompt and the worker
    # rewrote it a window at a time. The reviewer sees this on the job because a
    # merge across window seams is a different claim from a single exact match.
    chunked: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Job":
        known = {k: data.get(k) for k in cls.__dataclass_fields__ if k in data}
        return cls(**known)

    @property
    def summary(self) -> str:
        files = ", ".join(self.files[:3]) + ("..." if len(self.files) > 3 else "")
        return f"{len(self.edits)} edit(s) {files}".strip()


class JobStore:
    def __init__(self, directory: Path = JOBS_DIR) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, job_id: str) -> Path:
        return self.directory / f"{job_id}.json"

    def _lock_path(self, job_id: str) -> Path:
        return self.directory / f"{job_id}.lock"

    def create(self, **fields) -> Job:
        job_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        job = Job(
            id=job_id,
            created=datetime.now().isoformat(timespec="seconds"),
            stamp=fields.get("stamp", ""),
            **{k: v for k, v in fields.items() if k != "stamp" and k in Job.__dataclass_fields__ and k not in ("id", "created")},
        )
        self.save(job)
        return job

    def save(self, job: Job) -> None:
        data = json.dumps(job.to_dict(), indent=2)
        tmp = self._path(job.id).with_suffix(".json.tmp")
        tmp.write_text(data, encoding="utf-8")
        os.replace(tmp, self._path(job.id))

    def get(self, job_id: str) -> Job | None:
        path = self._path(job_id)
        if not path.exists():
            return None
        try:
            return Job.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, TypeError):
            return None

    def list(self) -> list[Job]:
        jobs = []
        for path in self.directory.glob("*.json"):
            job = self.get(path.stem)
            if job:
                jobs.append(job)
        return sorted(jobs, key=lambda j: j.created, reverse=True)

    def update(self, job_id: str, **fields) -> Job | None:
        job = self.get(job_id)
        if job is None:
            return None
        for key, value in fields.items():
            if key in Job.__dataclass_fields__:
                setattr(job, key, value)
        self.save(job)
        return job

    def claim(self, job_id: str) -> bool:
        """Atomically take ownership of a queued job. True when claimed."""
        try:
            fd = os.open(self._lock_path(job_id), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True

    def release(self, job_id: str) -> None:
        try:
            self._lock_path(job_id).unlink()
        except FileNotFoundError:
            pass

    def next_queued(self) -> Job | None:
        for job in reversed(self.list()):  # oldest first
            if job.status == "queued":
                return job
        return None
