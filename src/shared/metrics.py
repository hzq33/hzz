"""Minimal Prometheus metrics for Agent Server.

Fail-open: if prometheus_client is unavailable, /metrics returns a stub and
HTTP middleware still records no-ops.
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger("agent.metrics")

_PROM = False
try:
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        CollectorRegistry,
        Counter,
        Gauge,
        Histogram,
        generate_latest,
    )

    _PROM = True
except ImportError:  # pragma: no cover
    CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"
    CollectorRegistry = None  # type: ignore[misc, assignment]
    Counter = Gauge = Histogram = generate_latest = None  # type: ignore[misc, assignment]


_registry: object | None = None
http_requests_total = None
http_request_duration_seconds = None
active_sessions = None
jobs_terminal_total = None
jobs_orphans_total = None
jobs_in_flight = None
rate_limit_total = None
llm_failover_total = None
llm_circuit_events_total = None
rag_fallbacks_total = None
retrieval_relevance_total = None
tool_value_total = None
answer_coverage_total = None


def init_metrics(*, registry: object | None = None) -> None:
    """Create process-wide metric collectors (idempotent)."""
    global _registry, http_requests_total, http_request_duration_seconds
    global active_sessions, jobs_terminal_total, jobs_orphans_total, jobs_in_flight
    global rate_limit_total, llm_failover_total, llm_circuit_events_total
    global rag_fallbacks_total, retrieval_relevance_total, tool_value_total
    global answer_coverage_total

    if not _PROM:
        logger.warning("prometheus_client not installed — /metrics is a stub")
        return
    if http_requests_total is not None:
        return

    _registry = registry or CollectorRegistry()
    http_requests_total = Counter(
        "agent_http_requests_total",
        "Total HTTP requests",
        ["method", "path", "status"],
        registry=_registry,
    )
    http_request_duration_seconds = Histogram(
        "agent_http_request_duration_seconds",
        "HTTP request latency in seconds",
        ["method", "path"],
        buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 15.0, 60.0),
        registry=_registry,
    )
    active_sessions = Gauge(
        "agent_active_sessions",
        "In-memory conversation sessions",
        registry=_registry,
    )
    jobs_terminal_total = Counter(
        "agent_jobs_terminal_total",
        "Jobs reaching a terminal state",
        ["job_type", "state"],
        registry=_registry,
    )
    jobs_orphans_total = Counter(
        "agent_jobs_orphans_total",
        "Running jobs marked failed after process restart",
        registry=_registry,
    )
    jobs_in_flight = Gauge(
        "agent_jobs_in_flight",
        "In-process asyncio job tasks currently tracked by the runner",
        registry=_registry,
    )
    rate_limit_total = Counter(
        "agent_rate_limit_total",
        "Rejected requests due to rate limiting",
        ["key_type"],
        registry=_registry,
    )
    llm_failover_total = Counter(
        "agent_llm_failover_total",
        "Primary→fallback LLM switches",
        registry=_registry,
    )
    llm_circuit_events_total = Counter(
        "agent_llm_circuit_events_total",
        "LLM circuit breaker state transitions",
        ["state"],
        registry=_registry,
    )
    rag_fallbacks_total = Counter(
        "agent_rag_fallbacks_total",
        "RAG pipeline fallbacks / degradations",
        ["reason"],
        registry=_registry,
    )
    # ── 判断回收指标（LLM 后处理判断结果 → 在线评估）────────────
    retrieval_relevance_total = Counter(
        "agent_retrieval_relevance_total",
        "Retrieved fragment relevance verdicts from LLM post-processing",
        ["verdict"],  # relevant | irrelevant
        registry=_registry,
    )
    tool_value_total = Counter(
        "agent_tool_value_total",
        "world_knowledge tool query value verdicts",
        ["query_type", "verdict"],  # verdict: valuable | useless
        registry=_registry,
    )
    answer_coverage_total = Counter(
        "agent_answer_coverage_total",
        "Whether the retrieved+tool context could answer the user question",
        ["verdict"],  # answerable | unanswerable
        registry=_registry,
    )


def normalize_path(path: str) -> str:
    """Collapse high-cardinality path segments for label safety."""
    parts = [p for p in path.split("/") if p]
    out: list[str] = []
    for part in parts:
        if len(part) > 24 or any(ch.isdigit() for ch in part) and "-" in part or part.endswith(".json") or (len(part) >= 8 and "_" in part):
            out.append(":id")
        else:
            out.append(part)
    return "/" + "/".join(out) if out else "/"


def observe_http(method: str, path: str, status: int, duration: float) -> None:
    if http_requests_total is None or http_request_duration_seconds is None:
        return
    label_path = normalize_path(path)
    http_requests_total.labels(method=method, path=label_path, status=str(status)).inc()
    http_request_duration_seconds.labels(method=method, path=label_path).observe(duration)


def set_active_sessions(n: int) -> None:
    if active_sessions is not None:
        active_sessions.set(n)


def observe_job_terminal(job_type: str, state: str) -> None:
    if jobs_terminal_total is not None:
        jobs_terminal_total.labels(job_type=job_type or "unknown", state=state).inc()


def observe_job_orphans(count: int) -> None:
    if jobs_orphans_total is not None and count > 0:
        jobs_orphans_total.inc(count)


def set_jobs_in_flight(n: int) -> None:
    if jobs_in_flight is not None:
        jobs_in_flight.set(max(0, int(n)))


def observe_rate_limit(*, key_type: str = "unknown") -> None:
    if rate_limit_total is not None:
        rate_limit_total.labels(key_type=key_type or "unknown").inc()


def observe_llm_failover() -> None:
    if llm_failover_total is not None:
        llm_failover_total.inc()


def observe_llm_circuit(*, state: str) -> None:
    if llm_circuit_events_total is not None:
        llm_circuit_events_total.labels(state=state or "unknown").inc()


def observe_rag_fallback(*, reason: str) -> None:
    """RAG 管线降级计数（改写失败/路由降级/实体解析失败/Lance fallback 等）。"""
    if rag_fallbacks_total is not None:
        rag_fallbacks_total.labels(reason=reason or "unknown").inc()


def observe_retrieval_relevance(*, verdict: str) -> None:
    """回收：LLM 后处理对召回片段的关联性判断（relevant/irrelevant）。"""
    if retrieval_relevance_total is not None:
        retrieval_relevance_total.labels(verdict=verdict or "unknown").inc()


def observe_tool_value(*, query_type: str, verdict: str) -> None:
    """回收：LLM 后处理对 world_knowledge 查询结果的价值判断（valuable/useless）。"""
    if tool_value_total is not None:
        tool_value_total.labels(query_type=query_type or "unknown", verdict=verdict or "unknown").inc()


def observe_answer_coverage(*, verdict: str) -> None:
    """回收：检索+工具上下文最终能否回答用户问题（answerable/unanswerable）。"""
    if answer_coverage_total is not None:
        answer_coverage_total.labels(verdict=verdict or "unknown").inc()


def render_metrics() -> tuple[bytes, str]:
    """Return (body, content_type) for GET /metrics."""
    if not _PROM or _registry is None or generate_latest is None:
        body = (
            b"# HELP agent_metrics_available Whether prometheus_client is loaded\n"
            b"# TYPE agent_metrics_available gauge\n"
            b"agent_metrics_available 0\n"
        )
        return body, CONTENT_TYPE_LATEST
    return generate_latest(_registry), CONTENT_TYPE_LATEST


class Timer:
    """Simple wall-clock timer for middleware."""

    __slots__ = ("_start",)

    def __init__(self) -> None:
        self._start = time.perf_counter()

    def elapsed(self) -> float:
        return time.perf_counter() - self._start
