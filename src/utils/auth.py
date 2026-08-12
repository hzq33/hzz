"""Static Bearer token authentication helpers for the Agent API."""

from __future__ import annotations

import os
import secrets

from fastapi import HTTPException, Request


def get_api_token() -> str | None:
    """Return the configured API token, or None when unset/blank."""
    token = os.getenv("AGENT_API_TOKEN", "").strip()
    return token or None


def extract_bearer_token(authorization: str | None) -> str | None:
    """Extract the token from an Authorization header."""
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


def is_public_path(path: str) -> bool:
    """Health, probes, metrics scrape, and client telemetry are anonymous."""
    normalized = path.rstrip("/") or "/"
    if normalized in {
        "/api/v1/agent/health",
        "/api/v1/agent/health/live",
        "/api/v1/agent/health/ready",
        "/metrics",
    }:
        return True
    # Browser web-vitals posts; no secrets — avoid 401 noise in DevTools.
    if normalized.startswith("/api/v1/monitor/"):
        return True
    return False


def require_bearer_token(request: Request) -> None:
    """Reject requests without a valid Bearer token (fail-closed).

    When AGENT_API_TOKEN is unset, every non-public route is rejected so
    accidental open deployments cannot happen. CORS preflight (OPTIONS)
    is skipped so browsers can negotiate allowed origins.
    """
    if request.method == "OPTIONS":
        return
    if is_public_path(request.url.path):
        return

    expected = get_api_token()
    if expected is None:
        raise HTTPException(
            status_code=503,
            detail="Agent API not secured: set AGENT_API_TOKEN in the environment",
        )

    provided = extract_bearer_token(request.headers.get("Authorization"))
    if provided is None or not secrets.compare_digest(provided, expected):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized: valid Bearer token required",
            headers={"WWW-Authenticate": "Bearer"},
        )


def parse_cors_origins(raw: str | None = None) -> list[str]:
    """Parse CORS_ORIGINS into an explicit allow-list (no wildcards)."""
    value = raw if raw is not None else os.getenv("CORS_ORIGINS", "")
    origins = [part.strip() for part in value.split(",") if part.strip()]
    if not origins:
        return [
            "http://localhost:3000",
            "http://localhost:3001",
        ]
    if any(origin == "*" for origin in origins):
        raise ValueError("CORS_ORIGINS must be an explicit list; '*' is not allowed")
    return origins
