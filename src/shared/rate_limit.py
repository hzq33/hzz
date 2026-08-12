"""In-process rate limiting (token bucket) keyed by API token or client IP."""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request


@dataclass
class _Bucket:
    tokens: float
    updated_at: float


class TokenBucketLimiter:
    """Simple process-local token bucket."""

    def __init__(self, rate_per_sec: float, burst: float) -> None:
        self.rate = max(0.01, float(rate_per_sec))
        self.burst = max(1.0, float(burst))
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, cost: float = 1.0) -> bool:
        now = time.monotonic()
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket(tokens=self.burst, updated_at=now)
                self._buckets[key] = bucket
            elapsed = max(0.0, now - bucket.updated_at)
            bucket.tokens = min(self.burst, bucket.tokens + elapsed * self.rate)
            bucket.updated_at = now
            if bucket.tokens < cost:
                return False
            bucket.tokens -= cost
            return True


_limiter: TokenBucketLimiter | None = None
_limiter_lock = threading.Lock()


def get_limiter() -> TokenBucketLimiter | None:
    """Return limiter when AGENT_RATE_LIMIT_RPS > 0."""
    global _limiter
    raw = os.getenv("AGENT_RATE_LIMIT_RPS", "0").strip()
    try:
        rps = float(raw)
    except ValueError:
        rps = 0.0
    if rps <= 0:
        return None
    # TokenBucketLimiter 会把速率 clamp 到最低 0.01；重建比较必须用同一
    # 有效速率，否则 rps<0.01 时每次调用都重建 limiter（限流恒放行）。
    effective = max(0.01, rps)
    burst = float(os.getenv("AGENT_RATE_LIMIT_BURST", str(max(effective * 2, 5))))
    with _limiter_lock:
        if _limiter is None or abs(_limiter.rate - effective) > 1e-9:
            _limiter = TokenBucketLimiter(effective, burst)
        return _limiter


def client_key(request: Request) -> str:
    auth = request.headers.get("Authorization") or ""
    if auth.lower().startswith("bearer ") and len(auth) > 10:
        return "tok:" + auth[7:23]
    forwarded = request.headers.get("X-Forwarded-For") or ""
    if forwarded:
        return "ip:" + forwarded.split(",")[0].strip()
    if request.client:
        return "ip:" + (request.client.host or "unknown")
    return "ip:unknown"


def enforce_rate_limit(request: Request) -> None:
    """Raise HTTP 429 when over limit. No-op when disabled."""
    limiter = get_limiter()
    if limiter is None:
        return
    path = request.url.path or ""
    if path.endswith("/health/live") or path.endswith("/health/ready") or path == "/metrics":
        return
    key = client_key(request)
    if not limiter.allow(key):
        try:
            from src.shared.metrics import observe_rate_limit

            observe_rate_limit(key_type=key.split(":", 1)[0])
        except Exception:
            pass
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded. Slow down or raise AGENT_RATE_LIMIT_RPS.",
            headers={"Retry-After": "1"},
        )
