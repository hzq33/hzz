"""Factories for consistently configured shared LLM clients."""

from __future__ import annotations

from src.shared.llm import SharedLLMClient
from src.utils.config import AgentConfig


def create_shared_llm(
    config: AgentConfig,
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
    timeout: float = 25.0,
    endpoint: str | None = None,
) -> SharedLLMClient:
    """Create a shared client using the primary/fallback agent configuration.

    ``endpoint`` 指定 LLM 调用点（如 "agent_chat" / "impersonation_chat"）：
    若前端在设置页为该调用点配置了服务商/模型/key/参数，则**覆盖**默认配置
    （未配置项回退 config.yaml 默认）。``endpoint=None`` 保持原行为。
    """
    primary = config.primary_llm_config()
    fallback = config.fallback_llm_config()
    thinking_disabled = config.thinking_disabled

    if endpoint:
        try:
            from src.shared.llm_config import get_endpoint_config

            ep = get_endpoint_config(endpoint)
            if ep.get("enabled", True):
                # 覆盖 primary：服务商 base_url / 模型 / key
                if ep.get("base_url"):
                    primary = {**primary, "base_url": ep["base_url"]}
                if ep.get("model"):
                    primary = {**primary, "model": ep["model"]}
                if ep.get("api_key"):
                    primary = {**primary, "api_key": ep["api_key"]}
                # 覆盖通用参数（显式传参优先于配置）
                if temperature is None:
                    temperature = ep.get("temperature")
                if max_tokens is None:
                    max_tokens = ep.get("max_tokens")
                # 思考模式：off→禁用 / on→启用 / auto→跟随默认
                thinking = str(ep.get("thinking") or "auto").strip().lower()
                if thinking == "off":
                    thinking_disabled = True
                elif thinking == "on":
                    thinking_disabled = False
        except Exception:  # noqa: BLE001
            pass

    return SharedLLMClient(
        primary=primary,
        fallback=fallback,
        temperature=config.temperature if temperature is None else temperature,
        max_tokens=config.max_tokens if max_tokens is None else max_tokens,
        timeout=timeout,
        max_retries=max(0, config.max_retries - 1) if config.max_retries else 0,
        thinking_disabled=thinking_disabled,
    )
