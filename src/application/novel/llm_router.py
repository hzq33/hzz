"""LLM-based intent router — replaces regex keyword matching with LLM classification.

Routes user queries to the correct RAG channel by classifying intent type
and extracting target characters via a single LLM call.

Drop-in replacement for ``IntentRouter`` — same ``classify()`` → ``IntentResult`` API.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import TYPE_CHECKING, Any, ClassVar

from src.shared.defaults import DEFAULT_DEEPSEEK_BASE_URL, DEFAULT_DEEPSEEK_MODEL

from src.application.novel.intent_router import IntentResult
from src.domain.novel.models import (
    BLOCK_CHARACTER,
    BLOCK_DIALOGUE,
    BLOCK_NARRATIVE,
    BLOCK_QA,
)

if TYPE_CHECKING:
    from src.application.novel.entity_resolver import QueryContext

logger = logging.getLogger("agent.llm_router")


def observe_rag_fallback(reason: str) -> None:
    """RAG 降级计数（prometheus 未初始化时 no-op）。"""
    try:
        from src.shared.metrics import observe_rag_fallback as _obs

        _obs(reason=reason)
    except Exception:
        pass

_PROMPT = """你是轻小说角色扮演助手的查询路由器。根据用户消息判断查询意图类型和涉及的角色。

意图类型（选一个）：
- chitchat: 寒暄问候、无实际信息需求（“你好”“在吗”“谢谢”）
- fact: 询问事实信息（谁、什么、哪里、什么时候、怎么样）
- relationship: 询问角色之间的关系（关系、相处、敌对、结盟）
- plot: 询问剧情发展、事件经过
- persona: 询问角色性格、设定、背景
- imitation: 要求以某角色口吻说话/扮演（“用XX的语气”“模仿XX”“XX会怎么说”）
- narrative: 要求原文引用、描写、场景描述

已知角色：{characters}
{alias_hints}
用户消息：{query}

只输出 JSON，不要其他内容：
{{"intent": "fact", "characters": ["角色名1"], "is_imitation": false}}
"""

# alias_hints 段落模板（当 QueryContext 提供时注入）
_ALIAS_HINTS_SECTION = """
已知称谓映射（query 中的称谓 → 规范角色名，请用规范名输出 characters 字段）：
{alias_hints}
"""

_ROUTER_MODEL = DEFAULT_DEEPSEEK_MODEL
_ROUTER_MAX_TOKENS = 400  # 推理模型思维链消耗，需足够配额
_ROUTER_TEMPERATURE = 0.0

# intent → channel_weights (qa 暂屏蔽)
_INTENT_WEIGHTS: dict[str, dict[str, float]] = {
    "chitchat":    {BLOCK_DIALOGUE: 1.0},
    "fact":        {BLOCK_NARRATIVE: 0.60, BLOCK_DIALOGUE: 0.25, BLOCK_CHARACTER: 0.15},
    "relationship": {BLOCK_CHARACTER: 0.50, BLOCK_NARRATIVE: 0.35, BLOCK_DIALOGUE: 0.15},
    "plot":        {BLOCK_NARRATIVE: 0.55, BLOCK_DIALOGUE: 0.25, BLOCK_CHARACTER: 0.20},
    "persona":     {BLOCK_NARRATIVE: 0.50, BLOCK_DIALOGUE: 0.35, BLOCK_CHARACTER: 0.15},
    "imitation":   {BLOCK_DIALOGUE: 1.0},
    "narrative":   {BLOCK_NARRATIVE: 1.0},
}

_INTENT_PRIMARY: dict[str, str] = {
    "chitchat": BLOCK_DIALOGUE,
    "fact": BLOCK_NARRATIVE,
    "relationship": BLOCK_CHARACTER,
    "plot": BLOCK_NARRATIVE,
    "persona": BLOCK_NARRATIVE,
    "imitation": BLOCK_DIALOGUE,
    "narrative": BLOCK_NARRATIVE,
}


def _endpoint_overrides() -> dict:
    """读取前端 llm-config 的 intent_router 覆盖（失败回退空）。"""
    try:
        from src.shared.llm_config import get_endpoint_config

        return get_endpoint_config("intent_router")
    except Exception:  # noqa: BLE001
        return {}


def _build_client():
    """Build sync OpenAI client for synchronous classify()."""
    from openai import OpenAI

    ep = _endpoint_overrides()
    api_key = (ep.get("api_key") or "").strip() or os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY not set")
    base_url = (ep.get("base_url") or "").strip() or os.getenv(
        "DEEPSEEK_BASE_URL", DEFAULT_DEEPSEEK_BASE_URL
    ).strip().rstrip("/")
    _http_kwargs: dict[str, Any] = {}
    try:
        import httpx

        _http_kwargs["http_client"] = httpx.Client(timeout=60, trust_env=False)
    except Exception:  # noqa: BLE001
        pass
    return OpenAI(
        api_key=api_key,
        base_url=f"{base_url}/v1" if not base_url.endswith(("/v1", "/v4")) else base_url,
        **_http_kwargs,
    )


def _build_async_client():
    """Build async OpenAI client for aclassify() (non-blocking in event loop)."""
    from openai import AsyncOpenAI

    ep = _endpoint_overrides()
    api_key = (ep.get("api_key") or "").strip() or os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY not set")
    base_url = (ep.get("base_url") or "").strip() or os.getenv(
        "DEEPSEEK_BASE_URL", DEFAULT_DEEPSEEK_BASE_URL
    ).strip().rstrip("/")
    _http_kwargs: dict[str, Any] = {}
    try:
        import httpx

        _http_kwargs["http_client"] = httpx.AsyncClient(timeout=60, trust_env=False)
    except Exception:  # noqa: BLE001
        pass
    return AsyncOpenAI(
        api_key=api_key,
        base_url=f"{base_url}/v1" if not base_url.endswith(("/v1", "/v4")) else base_url,
        **_http_kwargs,
    )


class LLMIntentRouter:
    """LLM-based intent classifier replacing regex IntentRouter."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ):
        self.enabled = enabled
        ep = _endpoint_overrides()
        self._model = model or ep.get("model") or _ROUTER_MODEL
        self._max_tokens = max_tokens or ep.get("max_tokens") or _ROUTER_MAX_TOKENS
        self._temperature = (
            temperature
            if temperature is not None
            else float(ep.get("temperature", _ROUTER_TEMPERATURE))
        )
        self._client = None
        self._async_client = None
        # Fallback regex router for when LLM is unavailable
        self._fallback: object | None = None

    def _get_fallback(self):
        if self._fallback is None:
            from src.application.novel.intent_router import IntentRouter
            self._fallback = IntentRouter()
        return self._fallback

    def _get_client(self):
        if self._client is None:
            self._client = _build_client()
        return self._client

    def _get_async_client(self):
        if self._async_client is None:
            self._async_client = _build_async_client()
        return self._async_client

    def _build_prompt(
        self,
        query: str,
        chars: list[str],
        query_context: "QueryContext | None",
    ) -> str:
        """构建 LLM prompt，注入 alias_hints（若 QueryContext 提供）。"""
        alias_section = ""
        if query_context and query_context.alias_hints:
            alias_section = _ALIAS_HINTS_SECTION.format(alias_hints=query_context.alias_hints)
        return _PROMPT.format(
            characters=", ".join(chars[:20]) if chars else "（未知）",
            alias_hints=alias_section,
            query=query.strip(),
        )

    def classify(
        self,
        query: str,
        available_characters: list[str] | None = None,
        query_context: "QueryContext | None" = None,
    ) -> IntentResult:
        """Classify query intent via LLM (sync). Falls back to regex on failure.

        Prefer ``aclassify`` inside async code — this sync variant blocks the
        event loop while awaiting the LLM HTTP call.

        Args:
            query: 用户原始 query
            available_characters: 已知角色名单
            query_context: EntityResolver 产出的查询上下文（含 alias_hints、resolved_entities）。
                           若提供，prompt 注入称谓映射，且 resolved_entities 直接作为
                           target_characters 候选，LLM 只需确认意图。
        """
        if not self.enabled or not query or not query.strip():
            return self._get_fallback().classify(query, available_characters)  # type: ignore[union-attr]

        chars = available_characters or []
        prompt = self._build_prompt(query, chars, query_context)
        try:
            from src.shared.llm_config import thinking_extra_body
            extra = thinking_extra_body("intent_router")
            resp = self._get_client().chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                temperature=self._temperature,
                max_tokens=self._max_tokens,
                **( {"extra_body": extra} if extra else {} ),
            )
            return self._build_result(resp.choices[0].message.content or "", query, chars, query_context)
        except Exception as exc:
            logger.warning("LLM router failed (%s); falling back to regex", exc)
            observe_rag_fallback("intent_router_error")
            return self._get_fallback().classify(query, available_characters)  # type: ignore[union-attr]

    async def aclassify(
        self,
        query: str,
        available_characters: list[str] | None = None,
        query_context: "QueryContext | None" = None,
    ) -> IntentResult:
        """Classify query intent via LLM (async, non-blocking).

        Async counterpart of ``classify`` — safe inside an event loop.

        Args:
            query: 用户原始 query
            available_characters: 已知角色名单
            query_context: EntityResolver 产出的查询上下文（含 alias_hints、resolved_entities）。
        """
        if not self.enabled or not query or not query.strip():
            return self._get_fallback().classify(query, available_characters)  # type: ignore[union-attr]

        chars = available_characters or []
        prompt = self._build_prompt(query, chars, query_context)
        try:
            from src.shared.llm_config import thinking_extra_body
            extra = thinking_extra_body("intent_router")
            resp = await self._get_async_client().chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                temperature=self._temperature,
                max_tokens=self._max_tokens,
                **( {"extra_body": extra} if extra else {} ),
            )
            return self._build_result(resp.choices[0].message.content or "", query, chars, query_context)
        except Exception as exc:
            logger.warning("LLM router aclassify failed (%s); falling back to regex", exc)
            observe_rag_fallback("intent_router_error")
            return self._get_fallback().classify(query, available_characters)  # type: ignore[union-attr]

    def _build_result(
        self,
        raw: str,
        query: str,
        chars: list[str],
        query_context: "QueryContext | None" = None,
    ) -> IntentResult:
        """Parse LLM output into IntentResult; falls back to regex on parse failure.

        若 query_context 提供 resolved_entities，将其作为 target_characters 候选
        与 LLM 输出合并（EntityResolver 确定性解析的实体优先，LLM 输出补充）。
        """
        raw = raw or ""
        finish = ""

        if not raw.strip():
            logger.warning("LLM router returned empty content; falling back to regex")
            observe_rag_fallback("intent_router_empty")
            return self._get_fallback().classify(query, chars)  # type: ignore[union-attr]

        result = _parse_router_json(raw)
        if result is None:
            logger.warning("LLM router parse failed; falling back to regex")
            observe_rag_fallback("intent_router_parse")
            return self._get_fallback().classify(query, chars)  # type: ignore[union-attr]

        intent = result.get("intent", "fact")
        target = list(result.get("characters") or [])
        is_imitation = bool(result.get("is_imitation", False))

        # Validate target characters against known list
        valid_targets = [c for c in target if any(c in k or k in c for k in chars)] if chars else target

        # 合并 QueryContext 的 resolved_entities（确定性解析优先）
        if query_context and query_context.resolved_entities:
            resolved_names = [e.canonical_name for e in query_context.resolved_entities]
            # resolved 实体优先加入，LLM 输出补充（去重）
            for name in resolved_names:
                if name and name not in valid_targets:
                    valid_targets.insert(0, name)

        weights = dict(_INTENT_WEIGHTS.get(intent, _INTENT_WEIGHTS["fact"]))
        primary = _INTENT_PRIMARY.get(intent, BLOCK_NARRATIVE)

        return IntentResult(
            primary_channel=primary,
            channel_weights=weights,
            filters={"characters": valid_targets} if valid_targets else {},
            confidence=0.80,
            is_imitation=is_imitation,
            target_characters=valid_targets,
        )


def _parse_router_json(raw: str) -> dict | None:
    """Parse LLM router output into a dict. Robust against thinking-chain wrapping."""
    text = raw.strip()

    # Direct JSON
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "intent" in data:
            return data
    except (json.JSONDecodeError, TypeError):
        pass

    # Extract JSON object from text (思维链 wrapping)
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, dict) and "intent" in data:
                return data
        except (json.JSONDecodeError, TypeError):
            pass

    # Markdown code fence
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(1))
            if isinstance(data, dict) and "intent" in data:
                return data
        except (json.JSONDecodeError, TypeError):
            pass

    return None
