"""Shared LLM client — unified OpenAI-compatible client with primary/fallback.

Supports sync, async, and streaming operations with automatic
primary/fallback switching, HTTP timeouts, and limited retries.
Also provides an OpenAI-native tool-calling loop (`achat_with_tools`).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator, Awaitable, Callable, Generator
from dataclasses import dataclass, field
from typing import (
    Any,
)

from openai import AsyncOpenAI, OpenAI

from src.shared.llm_resilience import LLMResilienceMixin

from src.shared.defaults import DEFAULT_DEEPSEEK_BASE_URL, DEFAULT_DEEPSEEK_MODEL

logger = logging.getLogger("agent")


@dataclass
class ToolInvocation:
    """One executed tool call inside a native tool loop."""

    id: str
    name: str
    arguments: dict[str, Any]
    result: str
    success: bool = True


@dataclass
class ToolLoopResult:
    """Result of `SharedLLMClient.achat_with_tools`."""

    content: str
    messages: list[dict[str, Any]]
    invocations: list[ToolInvocation] = field(default_factory=list)


@dataclass
class LLMTextResult:
    """Text completion with finish_reason for truncation detection."""

    content: str
    finish_reason: str = ""
    model: str = ""


class SharedLLMClient(LLMResilienceMixin):
    """Unified LLM client with primary/fallback failover."""

    REVERT_COOLDOWN = 20.0

    def __init__(
        self,
        primary: dict[str, Any] | None = None,
        fallback: dict[str, Any] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        timeout: float = 25.0,
        max_retries: int = 2,
        thinking_disabled: bool | None = None,
    ):
        self._primary_config = dict(primary or {})
        self._fallback_config = dict(fallback or {})

        if not self._primary_config:
            self._primary_config = {
                "base_url": DEFAULT_DEEPSEEK_BASE_URL,
                "api_key": "",
                "model": DEFAULT_DEEPSEEK_MODEL,
            }

        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = float(timeout)
        self.max_retries = max(0, int(max_retries))
        self.model = self._primary_config.get("model", DEFAULT_DEEPSEEK_MODEL)
        # None → auto-detect DeepSeek endpoints; True/False → explicit override.
        self._thinking_disabled = thinking_disabled

        self._using_fallback = False
        self._fallback_since: float | None = None
        self._probe_failures = 0
        self._last_usage: dict | None = None
        self._consecutive_failures = 0
        self._circuit_open_until: float = 0.0

        self._sync_client: OpenAI | None = None
        self._async_client: AsyncOpenAI | None = None
        self._build_client()

    @property
    def base_url(self) -> str:
        return self._active_config.get("base_url", "")

    @property
    def _active_config(self) -> dict:
        return self._fallback_config if self._using_fallback else self._primary_config

    @property
    def async_client(self) -> AsyncOpenAI:
        return self._async_client  # type: ignore[return-value]

    @property
    def sync_client(self) -> OpenAI:
        return self._sync_client  # type: ignore[return-value]

    @property
    def last_usage(self) -> dict | None:
        return self._last_usage

    def _build_client(self):
        cfg = self._active_config
        api_key = cfg.get("api_key") or "not-needed"
        base_url = cfg.get("base_url")
        model = cfg.get("model")

        kwargs: dict[str, Any] = {
            "api_key": api_key,
            "timeout": self.timeout,
            "max_retries": 0,
        }
        if base_url:
            kwargs["base_url"] = base_url
        # 直连 API：禁用系统代理（httpx 默认读 Windows 系统代理会把请求转发到本地
        # Clash 等端口导致 Connection error）。openai>=1.0 用 http_client 注入。
        try:
            import httpx

            kwargs["http_client"] = httpx.Client(
                timeout=self.timeout, trust_env=False
            )
        except Exception:  # noqa: BLE001 - 不支持时回退默认客户端
            pass

        self._sync_client = OpenAI(**kwargs)
        kwargs_async = dict(kwargs)
        try:
            import httpx

            kwargs_async["http_client"] = httpx.AsyncClient(
                timeout=self.timeout, trust_env=False
            )
        except Exception:  # noqa: BLE001
            pass
        self._async_client = AsyncOpenAI(**kwargs_async)
        self.model = model or self.model

    # ── DeepSeek thinking-mode default (v4-flash consumes max_tokens on thinking) ─

    def _thinking_extra_body(self) -> dict:
        """Return the default ``extra_body`` for the active provider.

        ``None`` (auto) injects ``thinking.disabled`` only for DeepSeek
        endpoints (base_url or model name), so non-DeepSeek providers that
        reject unknown ``extra_body`` keys are never affected. Callers that
        pass their own ``extra_body`` keep it (merged, explicit keys win).
        """
        if self._thinking_disabled is False:
            return {}
        if self._thinking_disabled is True:
            return {"thinking": {"type": "disabled"}}
        cfg = self._active_config
        base = str(cfg.get("base_url") or "").lower()
        model = str(cfg.get("model") or "").lower()
        if "deepseek" in base or "deepseek" in model:
            return {"thinking": {"type": "disabled"}}
        return {}

    def _apply_defaults(self, kwargs: dict) -> dict:
        """Merge the DeepSeek thinking-disabled default into ``extra_body``.

        Returns a new dict; explicit ``extra_body`` passed by the caller is
        preserved and its keys take precedence over the default.
        """
        default = self._thinking_extra_body()
        if not default:
            return kwargs
        merged = dict(kwargs)
        eb = dict(merged.get("extra_body") or {})
        for key, value in default.items():
            eb.setdefault(key, value)
        if eb:
            merged["extra_body"] = eb
        return merged

    def chat(self, messages: list, **kwargs) -> str:
        kwargs = self._apply_defaults(kwargs)
        self._try_revert()
        self._ensure_circuit_allows()
        attempts = self.max_retries + 1
        last_exc: BaseException | None = None
        for attempt in range(attempts):
            try:
                response = self.sync_client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=kwargs.get("temperature", self.temperature),
                    max_tokens=kwargs.get("max_tokens", self.max_tokens),
                    **{k: v for k, v in kwargs.items() if k not in ("temperature", "max_tokens")},
                )
                self._log_usage(response)
                return response.choices[0].message.content or ""
            except Exception as e:
                last_exc = e
                if attempt < attempts - 1 and self._is_retryable(e):
                    logger.warning("LLM chat retry %d/%d: %s", attempt + 1, attempts, e)
                    time.sleep(self._backoff_seconds(attempt, e))
                    continue
                if self._on_failure(e):
                    return self.chat(messages, **kwargs)
                raise
        assert last_exc is not None
        raise last_exc

    def stream_chat(self, messages: list, **kwargs) -> Generator[str]:
        kwargs = self._apply_defaults(kwargs)
        self._last_usage = None
        self._try_revert()
        self._ensure_circuit_allows()

        def _inner_stream(*, allow_retry: bool = True) -> Generator[str]:
            chunk_count = 0
            try:
                response = self.sync_client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=kwargs.get("temperature", self.temperature),
                    max_tokens=kwargs.get("max_tokens", self.max_tokens),
                    stream=True,
                    stream_options={"include_usage": True},
                    **{k: v for k, v in kwargs.items() if k not in ("temperature", "max_tokens")},
                )
                for chunk in response:
                    if hasattr(chunk, "usage") and chunk.usage:
                        self._record_stream_usage(chunk.usage)
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    if delta and delta.content:
                        chunk_count += 1
                        yield delta.content

                if chunk_count == 0:
                    if allow_retry and self._on_failure(RuntimeError("empty stream")):
                        yield from _inner_stream(allow_retry=False)
                        return
                    content = self.chat(messages, **kwargs)
                    yield content
            except Exception as e:
                if allow_retry and chunk_count == 0 and self._on_failure(e):
                    yield from _inner_stream(allow_retry=False)
                    return
                if chunk_count == 0:
                    content = self.chat(messages, **kwargs)
                    yield content
                    return
                raise

        yield from _inner_stream()

    async def achat_result(self, messages: list, **kwargs) -> LLMTextResult:
        """Async chat returning content + finish_reason (for truncation detection)."""
        from src.shared.telemetry import span

        kwargs = self._apply_defaults(kwargs)
        await self._try_revert_async()
        self._ensure_circuit_allows()
        attempts = self.max_retries + 1
        last_exc: BaseException | None = None
        with span(
            "llm.achat",
            model=self.model,
            using_fallback=self._using_fallback,
            message_count=len(messages),
        ):
            for attempt in range(attempts):
                try:
                    response = await self.async_client.chat.completions.create(
                        model=self.model,
                        messages=messages,
                        temperature=kwargs.get("temperature", self.temperature),
                        max_tokens=kwargs.get("max_tokens", self.max_tokens),
                        **{
                            k: v
                            for k, v in kwargs.items()
                            if k not in ("temperature", "max_tokens")
                        },
                    )
                    self._log_usage(response, label="achat")
                    choice = response.choices[0] if response.choices else None
                    content = ""
                    finish_reason = ""
                    if choice is not None:
                        content = (choice.message.content or "") if choice.message else ""
                        finish_reason = str(getattr(choice, "finish_reason", "") or "")
                    return LLMTextResult(
                        content=content,
                        finish_reason=finish_reason,
                        model=str(getattr(response, "model", None) or self.model),
                    )
                except Exception as e:
                    last_exc = e
                    if attempt < attempts - 1 and self._is_retryable(e):
                        logger.warning(
                            "LLM achat retry %d/%d: %s", attempt + 1, attempts, e
                        )
                        await asyncio.sleep(self._backoff_seconds(attempt, e))
                        continue
                    if self._on_failure(e):
                        return await self.achat_result(messages, **kwargs)
                    raise
            assert last_exc is not None
            raise last_exc

    async def achat(self, messages: list, **kwargs) -> str:
        result = await self.achat_result(messages, **kwargs)
        return result.content

    async def achat_stream(
        self, messages: list, **kwargs
    ) -> AsyncGenerator[str]:
        kwargs = self._apply_defaults(kwargs)
        self._last_usage = None
        await self._try_revert_async()
        self._ensure_circuit_allows()

        async def _inner_stream(*, allow_retry: bool = True) -> AsyncGenerator[str]:
            chunk_count = 0
            try:
                stream = await self.async_client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=kwargs.get("temperature", self.temperature),
                    max_tokens=kwargs.get("max_tokens", self.max_tokens),
                    stream=True,
                    stream_options={"include_usage": True},
                    **{k: v for k, v in kwargs.items() if k not in ("temperature", "max_tokens")},
                )
                async for chunk in stream:
                    if hasattr(chunk, "usage") and chunk.usage:
                        self._record_stream_usage(chunk.usage)
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    if delta and delta.content:
                        chunk_count += 1
                        yield delta.content

                if chunk_count == 0:
                    if allow_retry and self._on_failure(RuntimeError("empty stream")):
                        async for tok in _inner_stream(allow_retry=False):
                            yield tok
                        return
                    content = await self.achat(messages, **kwargs)
                    yield content
            except Exception as e:
                if allow_retry and chunk_count == 0 and self._on_failure(e):
                    async for tok in _inner_stream(allow_retry=False):
                        yield tok
                    return
                if chunk_count == 0:
                    content = await self.achat(messages, **kwargs)
                    yield content
                    return
                raise

        async for tok in _inner_stream():
            yield tok

    async def achat_with_tools(
        self,
        messages: list,
        *,
        tools: list[dict[str, Any]],
        execute_tool: Callable[[str, dict[str, Any]], Awaitable[str]],
        max_rounds: int = 5,
        tool_choice: str | dict[str, Any] = "auto",
        on_tool: Callable[[ToolInvocation], Awaitable[None]] | None = None,
        max_tool_result_chars: int = 12000,
        **kwargs: Any,
    ) -> ToolLoopResult:
        """Run an OpenAI-native tool-calling loop.

        Calls chat.completions with ``tools=``, executes each ``tool_calls``
        entry via ``execute_tool(name, args)``, appends assistant/tool
        messages, and repeats until the model returns a final content reply
        or ``max_rounds`` is reached.

        Args:
            messages: Chat messages (will be copied; caller list is not mutated).
            tools: OpenAI tools schemas (from ToolRegistry.get_openai_functions).
            execute_tool: Async callback ``(name, args) -> result_text``.
            max_rounds: Maximum LLM↔tool rounds.
            tool_choice: Passed to the API (default ``\"auto\"``).
            on_tool: Optional async callback after each tool invocation.
            max_tool_result_chars: Truncate tool results fed back to the model.
            **kwargs: Extra chat.completions kwargs (temperature, max_tokens, …).

        Returns:
            ToolLoopResult with final content, full message list, and invocations.
        """
        from src.shared.telemetry import span

        kwargs = self._apply_defaults(kwargs)
        if not tools:
            content = await self.achat(messages, **kwargs)
            return ToolLoopResult(content=content, messages=list(messages), invocations=[])

        api_messages: list[dict[str, Any]] = [dict(m) for m in messages]
        invocations: list[ToolInvocation] = []
        await self._try_revert_async()
        self._ensure_circuit_allows()

        with span(
            "llm.achat_with_tools",
            model=self.model,
            tool_count=len(tools),
            max_rounds=max_rounds,
        ):
            for round_idx in range(max(1, max_rounds)):
                create_kwargs = {
                    k: v
                    for k, v in kwargs.items()
                    if k not in ("temperature", "max_tokens")
                }
                try:
                    response = await self.async_client.chat.completions.create(
                        model=self.model,
                        messages=api_messages,
                        tools=tools,
                        tool_choice=tool_choice,
                        temperature=kwargs.get("temperature", self.temperature),
                        max_tokens=kwargs.get("max_tokens", self.max_tokens),
                        **create_kwargs,
                    )
                except Exception as e:
                    if self._on_failure(e):
                        return await self.achat_with_tools(
                            api_messages,
                            tools=tools,
                            execute_tool=execute_tool,
                            max_rounds=max_rounds - round_idx,
                            tool_choice=tool_choice,
                            on_tool=on_tool,
                            max_tool_result_chars=max_tool_result_chars,
                            **kwargs,
                        )
                    raise

                self._log_usage(response, label="achat_with_tools")
                msg = response.choices[0].message
                tool_calls = getattr(msg, "tool_calls", None) or []

                if not tool_calls:
                    content = msg.content or ""
                    api_messages.append({"role": "assistant", "content": content})
                    return ToolLoopResult(
                        content=content,
                        messages=api_messages,
                        invocations=invocations,
                    )

                # Parallel tool_calls: one assistant message, then N tool messages.
                serialized_calls = []
                for tc in tool_calls:
                    serialized_calls.append(
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments or "{}",
                            },
                        }
                    )
                api_messages.append(
                    {
                        "role": "assistant",
                        "content": msg.content or "",
                        "tool_calls": serialized_calls,
                    }
                )

                for tc in tool_calls:
                    name = tc.function.name
                    raw_args = tc.function.arguments or "{}"
                    try:
                        args = json.loads(raw_args)
                        if not isinstance(args, dict):
                            args = {}
                    except json.JSONDecodeError:
                        args = {}
                        logger.warning(
                            "Invalid tool args JSON for %s: %s", name, raw_args[:120]
                        )

                    success = True
                    try:
                        result_text = await execute_tool(name, args)
                    except Exception as e:
                        success = False
                        result_text = f"Error: {e}"
                        logger.exception("Tool execution failed: %s", name)

                    if len(result_text) > max_tool_result_chars:
                        result_text = (
                            result_text[:max_tool_result_chars]
                            + f"…[tool output truncated, total {len(result_text)} chars]"
                        )

                    inv = ToolInvocation(
                        id=tc.id,
                        name=name,
                        arguments=args,
                        result=result_text,
                        success=success and not str(result_text).startswith("Error:"),
                    )
                    invocations.append(inv)
                    api_messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": result_text,
                        }
                    )
                    logger.info(
                        "Native tool round=%d tool=%s args=%s → %s",
                        round_idx + 1,
                        name,
                        str(args)[:80],
                        result_text[:80],
                    )
                    if on_tool is not None:
                        await on_tool(inv)

            # Max rounds exhausted — ask once more without tools for a final reply.
            logger.warning(
                "achat_with_tools hit max_rounds=%d; forcing final reply", max_rounds
            )
            content = await self.achat(api_messages, **kwargs)
            api_messages.append({"role": "assistant", "content": content})
            return ToolLoopResult(
                content=content,
                messages=api_messages,
                invocations=invocations,
            )

    async def close(self):
        try:
            if self._async_client is not None:
                await self._async_client.close()
        except Exception:
            pass
        try:
            if self._sync_client is not None:
                self._sync_client.close()
        except Exception:
            pass
        logger.debug("SharedLLMClient closed.")

    def is_using_fallback(self) -> bool:
        return self._using_fallback
