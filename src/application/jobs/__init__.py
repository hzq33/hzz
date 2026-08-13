"""Submit jobs to in-process runner or external worker queue."""

from __future__ import annotations

import logging
import os

from src.application.jobs.registry import ensure_builtin_handlers, get_handler
from src.shared.async_jobs import JobHandler, JobRecord, get_job_runner, get_job_store

logger = logging.getLogger("agent.jobs")


def worker_mode() -> str:
    """``inprocess`` (default) or ``external`` (API only enqueues pending)."""
    return os.getenv("AGENT_JOB_WORKER_MODE", "inprocess").strip().lower()


def is_external_worker_mode() -> bool:
    return worker_mode() in {"external", "outprocess", "worker", "queue"}


def submit_job(
    job: JobRecord,
    handler: JobHandler | None = None,
) -> JobRecord:
    """Persist job and either run in-process or leave pending for a worker.

    Prefer registry handlers so API and worker share the same code path.
    An explicit ``handler`` overrides the registry (in-process only).
    """
    ensure_builtin_handlers()
    store = get_job_store()
    if not job.state:
        job.state = "pending"
    store.save(job)

    if is_external_worker_mode():
        logger.info(
            "Job queued for external worker job_id=%s type=%s",
            job.job_id,
            job.job_type,
        )
        return job

    resolved = handler or get_handler(job.job_type)
    if resolved is None:
        job.state = "failed"
        job.error = f"no_handler:{job.job_type}"
        store.save(job)
        logger.error("No handler for job_type=%s job_id=%s", job.job_type, job.job_id)
        return job
    return get_job_runner().enqueue(job, resolved)
