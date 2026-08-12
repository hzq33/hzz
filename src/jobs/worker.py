"""Out-of-process async job worker (SQLite claim / lease).

Usage::

    # API process
    AGENT_JOB_WORKER_MODE=external
    uvicorn agent_server:app --port 8080

    # Worker process (same machine / shared AGENT_JOB_DB)
    python -m src.jobs.worker

Default remains in-process (no worker needed).
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
import uuid

from src.application.jobs.registry import ensure_builtin_handlers, get_handler
from src.shared.async_jobs import get_job_store
from src.shared.logging_config import configure_logging
from src.shared.metrics import observe_job_terminal
from src.shared.telemetry import init_telemetry, shutdown_telemetry, span

logger = logging.getLogger("agent.job_worker")

_STOP = asyncio.Event()


def _owner_id() -> str:
    host = socket.gethostname()[:24]
    return f"{host}:{os.getpid()}:{uuid.uuid4().hex[:6]}"


def _lease_sec() -> float:
    try:
        return max(30.0, float(os.getenv("AGENT_JOB_LEASE_SEC", "600")))
    except (TypeError, ValueError):
        return 600.0


def _poll_sec() -> float:
    try:
        return max(0.2, float(os.getenv("AGENT_JOB_WORKER_POLL_SEC", "1.0")))
    except (TypeError, ValueError):
        return 1.0


async def run_claimed_job(job, owner: str) -> None:
    ensure_builtin_handlers()
    store = get_job_store()
    handler = get_handler(job.job_type)
    if handler is None:
        job.state = "failed"
        job.error = f"no_handler:{job.job_type}"
        store.save(job)
        observe_job_terminal(job.job_type, job.state)
        return

    with span(
        "job.run",
        job_id=job.job_id,
        job_type=job.job_type,
        mode="external",
        lease_owner=owner,
    ):
        try:
            result = await handler(job)
            job.result = dict(result or {})
            terminal = job.result.pop("_state", None)
            job.state = str(terminal or "done")
            if job.state == "failed" and not job.error:
                job.error = job.result.get("error") or "failed"
        except asyncio.CancelledError:
            job.state = "failed"
            job.error = "cancelled_on_shutdown"
            store.save(job)
            observe_job_terminal(job.job_type, job.state)
            raise
        except Exception as exc:
            logger.exception("Worker job failed job_id=%s", job.job_id)
            job.state = "failed"
            job.error = str(exc)
        finally:
            store.save(job)
            observe_job_terminal(job.job_type, job.state)


async def worker_loop(*, owner: str | None = None) -> None:
    owner = owner or _owner_id()
    store = get_job_store()
    if not hasattr(store, "claim_next_pending"):
        raise RuntimeError(
            "Job store does not support claim_next_pending; use AGENT_JOB_BACKEND=sqlite"
        )
    logger.info(
        "Job worker started owner=%s poll=%.1fs lease=%.0fs",
        owner,
        _poll_sec(),
        _lease_sec(),
    )
    while not _STOP.is_set():
        try:
            job = store.claim_next_pending(owner=owner, lease_sec=_lease_sec())
        except Exception as exc:
            logger.warning("Claim failed: %s", exc)
            job = None
        if job is None:
            try:
                await asyncio.wait_for(_STOP.wait(), timeout=_poll_sec())
            except TimeoutError:
                pass
            continue
        logger.info("Claimed job_id=%s type=%s", job.job_id, job.job_type)
        await run_claimed_job(job, owner)


def main() -> None:
    configure_logging()
    init_telemetry(service_name=os.getenv("OTEL_SERVICE_NAME", "makers-agent-worker"))

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _ask_stop(*_args):
        logger.info("Shutdown signal received")
        loop.call_soon_threadsafe(_STOP.set)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _ask_stop)
        except (ValueError, OSError):
            pass

    try:
        loop.run_until_complete(worker_loop())
    finally:
        shutdown_telemetry()
        loop.close()


if __name__ == "__main__":
    main()
