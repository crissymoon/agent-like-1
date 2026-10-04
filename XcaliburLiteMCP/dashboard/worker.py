"""
Queue worker.

One background thread owns generation. When it is busy, new jobs wait in the
"queued" state, which is what keeps two edits from being generated against the
same file at once. The claim lock makes that guarantee hold even if a second
dashboard process is running.
"""
from __future__ import annotations

import threading
import time

from dashboard.jobs import JobStore
from engine import orchestrator


class Worker(threading.Thread):
    def __init__(self, store: JobStore, poll_seconds: float = 1.5) -> None:
        super().__init__(daemon=True, name="xcalibur-worker")
        self.store = store
        self.poll_seconds = poll_seconds
        self._stop = threading.Event()
        self.current_job_id: str = ""

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        while not self._stop.is_set():
            job = self.store.next_queued()
            if job is None:
                self._stop.wait(self.poll_seconds)
                continue
            if not self.store.claim(job.id):
                self._stop.wait(self.poll_seconds)
                continue
            self.current_job_id = job.id
            self.store.update(job.id, status="processing")
            orchestrator.process_job(self.store.get(job.id) or job, self.store)
            self.current_job_id = ""

    @property
    def busy(self) -> bool:
        return bool(self.current_job_id)


def start_worker(store: JobStore) -> Worker:
    worker = Worker(store)
    worker.start()
    return worker
