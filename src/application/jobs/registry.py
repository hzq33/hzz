"""Job type → handler registry (enables out-of-process workers)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from src.shared.async_jobs import JobRecord

JobHandler = Callable[[JobRecord], Awaitable[dict]]

_HANDLERS: dict[str, JobHandler] = {}


def register_handler(job_type: str, handler: JobHandler) -> None:
    key = (job_type or "").strip()
    if not key:
        raise ValueError("job_type required")
    _HANDLERS[key] = handler


def get_handler(job_type: str) -> JobHandler | None:
    return _HANDLERS.get((job_type or "").strip())


def known_job_types() -> list[str]:
    return sorted(_HANDLERS.keys())


def ensure_builtin_handlers() -> None:
    """Import side-effect registrations (idempotent)."""
    from src.application.jobs import handlers as _handlers  # noqa: F401

    _ = _handlers  # silence lint
