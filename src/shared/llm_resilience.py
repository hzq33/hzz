"""SharedLLMClient resilience mixin — circuit breaker, fallback, retry, usage.

Extracted from the former monolithic ``llm.py``; logic unchanged.
Mixin methods share instance state (``self._using_fallback`` / ``self._circuit_*``).
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from typing import Any, Tuple, Type

from openai import APIStatusError, OpenAI, RateLimitError

logger = logging.getLogger("agent")

_RETRYABLE: Tuple[Type[BaseException], ...] = (
    APIStatusError,
    RateLimitError,
    TimeoutError,
    ConnectionError,
)


class LLMResilienceMixin:
    """Circuit breaker / fallback / retry / usage-tracking methods."""


    @staticmethod
    def _is_retryable(exc: BaseException) -> bool:
        if isinstance(exc, _RETRYABLE):
            return True
        if isinstance(exc, APIStatusError) and exc.status_code >= 500:
            return True
        return False

    def _switch_to_fallback(self):
        if self._using_fallback:
            return
        if not self._fallback_config:
            return
        logger.warning(
            "Switching to fallback API: %s model=%s",
            self._fallback_config.get("base_url"),
            self._fallback_config.get("model"),
        )
        self._using_fallback = True
        self._fallback_since = time.time()
        self._probe_failures = 0
        self._build_client()
        try:
            from src.shared.metrics import observe_llm_failover

            observe_llm_failover()
        except Exception:
            pass

    def _circuit_threshold(self) -> int:
        import os

        try:
            return max(1, int(os.getenv("AGENT_LLM_CIRCUIT_FAILURES", "3")))
        except ValueError:
            return 3

    def _fallback_failure_threshold(self) -> int:
        """Consecutive call failures required before switching to fallback.

        Single transient failures retry in place (backoff) and stay on the
        primary; only sustained failures (default 2 in a row) trigger the
        expensive fallback switch.
        """
        import os

        try:
            return max(1, int(os.getenv("AGENT_LLM_FALLBACK_FAILURES", "2")))
        except ValueError:
            return 2

    def _circuit_open_sec(self) -> float:
        import os

        try:
            return max(0.0, float(os.getenv("AGENT_LLM_CIRCUIT_OPEN_SEC", "30")))
        except ValueError:
            return 30.0

    def _jitter_enabled(self) -> bool:
        import os

        return os.getenv("AGENT_LLM_RETRY_JITTER", "1").strip().lower() not in {
            "0",
            "false",
            "no",
            "off",
        }

    def _circuit_blocked(self) -> bool:
        return time.time() < float(self._circuit_open_until or 0.0)

    def _emit_circuit(self, state: str) -> None:
        try:
            from src.shared.metrics import observe_llm_circuit

            observe_llm_circuit(state=state)
        except Exception:
            pass

    def _open_circuit(self) -> None:
        sec = self._circuit_open_sec()
        if sec > 0:
            self._circuit_open_until = time.time() + sec
        self._emit_circuit("open")
        if self._fallback_config and not self._using_fallback:
            self._switch_to_fallback()

    def _ensure_circuit_allows(self) -> None:
        """Force fallback while open; raise if primary-only and still open."""
        if not self._circuit_blocked():
            return
        if self._fallback_config:
            if not self._using_fallback:
                self._switch_to_fallback()
            return
        raise RuntimeError(
            f"LLM circuit open for {max(0.0, self._circuit_open_until - time.time()):.0f}s "
            "(no fallback configured)"
        )

    def _note_success(self) -> None:
        self._consecutive_failures = 0
        if self._circuit_open_until and not self._using_fallback:
            self._circuit_open_until = 0.0
            self._emit_circuit("closed")

    def _note_failure(self, exc: BaseException) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures < self._circuit_threshold():
            return
        logger.warning(
            "LLM circuit opening after %d failures (%s)",
            self._consecutive_failures,
            exc,
        )
        self._open_circuit()

    def _retry_after_seconds(self, exc: BaseException) -> float | None:
        headers = getattr(exc, "headers", None) or getattr(
            getattr(exc, "response", None), "headers", None
        )
        if not headers:
            return None
        try:
            raw = headers.get("Retry-After") or headers.get("retry-after")
        except Exception:
            raw = None
        if raw is None:
            return None
        try:
            return max(0.0, float(raw))
        except (TypeError, ValueError):
            return None

    def _backoff_seconds(
        self, attempt: int, exc: BaseException | None = None
    ) -> float:
        import random

        retry_after = self._retry_after_seconds(exc) if exc is not None else None
        if retry_after is not None:
            base = min(max(retry_after, 0.1), 30.0)
        else:
            base = float(min(2 ** attempt, 4))
        if self._jitter_enabled():
            return base * (0.5 + random.random())
        return base

    def _on_failure(self, exc: BaseException) -> bool:
        """Record failure; return True if caller should retry on fallback.

        A single failed call (after in-place retries) does NOT switch to the
        fallback — consecutive failures must reach ``_fallback_failure_threshold``
        first, so transient API hiccups never pay the expensive fallback switch.
        """
        was_fallback = self._using_fallback
        self._note_failure(exc)
        if self._using_fallback and not was_fallback:
            return True
        if not self._using_fallback and self._fallback_config:
            if self._consecutive_failures >= self._fallback_failure_threshold():
                self._switch_to_fallback()
                return True
            logger.warning(
                "LLM failure %d/%d on primary (no fallback switch yet): %s",
                self._consecutive_failures,
                self._fallback_failure_threshold(),
                exc,
            )
        return False

    def _probe_backoff(self) -> float:
        """Exponential backoff between primary-API probes (capped at 80s)."""
        return min(self.REVERT_COOLDOWN * (2 ** self._probe_failures), 80.0)

    async def _try_revert_async(self) -> None:
        """Async variant: run the (blocking) primary-API probe in a worker thread.

        The sync ``_try_revert`` performs a blocking ``models.list()`` network
        call (timeout up to 10s); running it directly on the event loop would
        stall every concurrent request while the primary is down. Only the
        async paths (``achat*``) call this; sync paths (``chat``/``stream_chat``)
        keep the blocking probe since they are not event-loop-bound.
        """
        await asyncio.to_thread(self._try_revert)

    def _try_revert(self):
        if self._circuit_blocked() and not self._using_fallback:
            # Still open on primary-only path — skip probe
            return
        if not self._using_fallback or self._fallback_since is None:
            return
        if time.time() - self._fallback_since < self._probe_backoff():
            return

        try:
            probe_client = OpenAI(
                api_key=self._primary_config.get("api_key") or "not-needed",
                base_url=self._primary_config.get("base_url"),
                timeout=min(10.0, self.timeout),
                max_retries=0,
            )
            probe_client.models.list()
            logger.info("Primary API recovered, reverting.")
            self._using_fallback = False
            self._fallback_since = None
            self._consecutive_failures = 0
            self._circuit_open_until = 0.0
            self._probe_failures = 0
            self._build_client()
            self._emit_circuit("closed")
        except Exception:
            # Probe failed — back off harder before the next attempt.
            self._probe_failures += 1
            self._fallback_since = time.time()

    def _log_usage(self, response, label: str = ""):
        if not hasattr(response, "usage") or not response.usage:
            return
        usage = response.usage
        from src.shared.llm_pricing import enrich_usage

        self._last_usage = enrich_usage(
            {
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "total_tokens": getattr(usage, "total_tokens", 0),
            },
            model=self.model,
        )
        self._consecutive_failures = 0
        tag = f"({label})" if label else ""
        cost = (self._last_usage or {}).get("cost_usd", 0)
        self._note_success()
        logger.info(
            "[LLM usage] %s model=%s prompt=%d completion=%d total=%d cost_usd=%.6f",
            tag,
            self.model,
            usage.prompt_tokens,
            usage.completion_tokens,
            getattr(usage, "total_tokens", 0),
            cost,
        )
        try:
            from src.shared.telemetry import trace_llm

            trace_llm(
                name=f"llm.{label or 'chat'}",
                model=self.model,
                prompt_tokens=usage.prompt_tokens or 0,
                completion_tokens=usage.completion_tokens or 0,
                total_tokens=getattr(usage, "total_tokens", 0) or 0,
                metadata={"fallback": self._using_fallback, "cost_usd": cost},
            )
        except Exception:
            pass

    def _record_stream_usage(self, usage_obj) -> None:
        if not usage_obj:
            return
        from src.shared.llm_pricing import enrich_usage

        self._last_usage = enrich_usage(
            {
                "prompt_tokens": usage_obj.prompt_tokens,
                "completion_tokens": usage_obj.completion_tokens,
                "total_tokens": getattr(usage_obj, "total_tokens", 0),
            },
            model=self.model,
        )
        self._note_success()
