"""HTTP response helpers that avoid leaking internal exception details."""

from __future__ import annotations

import logging

from fastapi import HTTPException

logger = logging.getLogger("agent_server")


def raise_internal_error(
    exc: BaseException,
    *,
    public_detail: str = "Internal server error",
    status_code: int = 500,
    log_message: str | None = None,
) -> None:
    """Log the real exception and raise a sanitized HTTPException."""
    logger.exception(log_message or public_detail)
    raise HTTPException(status_code=status_code, detail=public_detail) from exc
