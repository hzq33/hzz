"""Lightweight observability — OpenTelemetry + optional Langfuse.

Designed to be fail-open: if SDKs are missing or disabled, calls become
no-ops so unit tests and local prototypes keep working.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger("agent.telemetry")

_INITIALIZED = False
_TRACER = None
_LANGFUSE = None


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def telemetry_enabled() -> bool:
    """Return True when tracing should be attempted."""
    if _truthy(os.getenv("OTEL_SDK_DISABLED")):
        return False
    if _truthy(os.getenv("TELEMETRY_ENABLED")):
        return True
    # Auto-enable when an OTLP endpoint or Langfuse key is configured.
    return bool(
        os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
        or os.getenv("LANGFUSE_PUBLIC_KEY")
    )


def init_telemetry(service_name: str = "makers-agent") -> bool:
    """Initialize OTel (and Langfuse if configured). Safe to call multiple times."""
    global _INITIALIZED, _TRACER, _LANGFUSE
    if _INITIALIZED:
        return _TRACER is not None or _LANGFUSE is not None
    _INITIALIZED = True

    if not telemetry_enabled():
        logger.info("Telemetry disabled (set TELEMETRY_ENABLED=true to enable)")
        return False

    ok = False

    # ── OpenTelemetry ─────────────────────────────────────
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

        resource = Resource.create(
            {
                "service.name": os.getenv("OTEL_SERVICE_NAME", service_name),
                "service.version": os.getenv("OTEL_SERVICE_VERSION", "1.0.0"),
            }
        )
        provider = TracerProvider(resource=resource)

        endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
        if endpoint:
            try:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                    OTLPSpanExporter,
                )

                provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
                logger.info("OTel OTLP exporter → %s", endpoint)
            except Exception as e:
                logger.warning("OTLP exporter unavailable (%s); falling back to console", e)
                provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        elif _truthy(os.getenv("OTEL_CONSOLE_EXPORT", "false")):
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

        trace.set_tracer_provider(provider)
        _TRACER = trace.get_tracer(service_name)
        ok = True
    except ImportError:
        logger.info(
            "opentelemetry packages not installed — install with: "
            "pip install opentelemetry-sdk opentelemetry-exporter-otlp"
        )
    except Exception as e:
        logger.warning("Failed to init OpenTelemetry: %s", e)

    # ── Langfuse (optional) ───────────────────────────────
    public_key = os.getenv("LANGFUSE_PUBLIC_KEY", "").strip()
    secret_key = os.getenv("LANGFUSE_SECRET_KEY", "").strip()
    if public_key and secret_key:
        try:
            from langfuse import Langfuse

            _LANGFUSE = Langfuse(
                public_key=public_key,
                secret_key=secret_key,
                host=os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"),
            )
            logger.info("Langfuse client initialized")
            ok = True
        except ImportError:
            logger.info("langfuse not installed — pip install langfuse")
        except Exception as e:
            logger.warning("Failed to init Langfuse: %s", e)

    return ok


def get_tracer():
    """Return the active tracer, or None."""
    return _TRACER


def instrument_fastapi(app: Any) -> bool:
    """Optionally wrap FastAPI with OTel auto-instrumentation (fail-open)."""
    if not telemetry_enabled():
        return False
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)
        logger.info("FastAPI OTel instrumentation enabled")
        return True
    except ImportError:
        logger.info(
            "opentelemetry-instrumentation-fastapi not installed — "
            "pip install opentelemetry-instrumentation-fastapi"
        )
        return False
    except Exception as exc:
        logger.warning("FastAPI OTel instrumentation failed: %s", exc)
        return False


def get_langfuse():
    """Return the Langfuse client, or None."""
    return _LANGFUSE


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[Any]:
    """Context manager for a tracing span (no-op when telemetry is inactive)."""
    tracer = _TRACER
    if tracer is None:
        yield None
        return

    with tracer.start_as_current_span(name) as current:
        for key, value in attributes.items():
            if value is None:
                continue
            try:
                current.set_attribute(key, value)
            except Exception:
                current.set_attribute(key, str(value))
        try:
            yield current
        except Exception as e:
            try:
                current.record_exception(e)
                current.set_attribute("error", True)
            except Exception:
                pass
            raise


def trace_llm(
    *,
    name: str = "llm.chat",
    model: str = "",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
    metadata: dict | None = None,
) -> None:
    """Record an LLM generation observation (Langfuse when available).

    Compatible with Langfuse SDK v4 (`start_observation`) and older
    clients that still expose `.generation()`.
    """
    if _LANGFUSE is None:
        return
    try:
        usage_details = {
            "input": int(prompt_tokens or 0),
            "output": int(completion_tokens or 0),
            "total": int(total_tokens or 0),
        }
        # Langfuse v3+/v4
        start_obs = getattr(_LANGFUSE, "start_observation", None)
        if callable(start_obs):
            obs = start_obs(
                name=name,
                as_type="generation",
                model=model or None,
                usage_details=usage_details,
                metadata=metadata or {},
            )
            end = getattr(obs, "end", None)
            if callable(end):
                end()
            return
        # Legacy Langfuse v2
        generation = getattr(_LANGFUSE, "generation", None)
        if callable(generation):
            generation(
                name=name,
                model=model or None,
                usage=usage_details,
                metadata=metadata or {},
            )
    except Exception as e:
        logger.debug("Langfuse generation failed: %s", e)


def shutdown_telemetry() -> None:
    """Flush exporters on process shutdown."""
    global _LANGFUSE
    if _LANGFUSE is not None:
        try:
            _LANGFUSE.flush()
        except Exception:
            pass
        _LANGFUSE = None
    try:
        from opentelemetry import trace

        provider = trace.get_tracer_provider()
        shutdown = getattr(provider, "shutdown", None)
        if callable(shutdown):
            shutdown()
    except Exception:
        pass
