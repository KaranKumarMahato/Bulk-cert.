"""Background worker: a single thread that processes queued generation jobs.

Why a dedicated worker thread (see README "Bulk processing" for the full
reasoning): FastAPI's ``BackgroundTasks`` are tied to the request/response
cycle of a single request and offer no queueing discipline, while Celery/RQ
would add external broker infrastructure that is out of scope for this
exercise. A plain ``queue.Queue`` + worker thread decouples HTTP acceptance
from generation, preserves ordering, keeps the event loop free, and can be
swapped for a distributed queue later without touching the service layer.
"""

from __future__ import annotations

import logging
import queue
import threading

from sqlalchemy.orm import sessionmaker

from .config import Settings
from .services import process_job

logger = logging.getLogger(__name__)


class CertificateWorker:
    """Single background thread that drains a FIFO queue of job ids."""

    def __init__(self, session_factory: sessionmaker, settings: Settings) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._queue: queue.Queue[str] = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the worker thread (no-op if it is already running)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="certificate-worker", daemon=True
        )
        self._thread.start()

    def submit(self, job_id: str) -> None:
        """Enqueue a job for asynchronous processing."""
        self._queue.put(job_id)

    def shutdown(self, timeout: float = 5.0) -> None:
        """Signal the worker to stop and wait briefly for it to finish."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                job_id = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                process_job(job_id, self._session_factory, self._settings)
            except Exception:  # noqa: BLE001 - never kill the worker thread
                logger.exception("unhandled error while processing job %s", job_id)
            finally:
                self._queue.task_done()
