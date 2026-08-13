"""Estimate USD cost from token usage (configurable via env)."""

from __future__ import annotations

import os
from typing import Any

# Default DeepSeek list prices (USD / 1M tokens). Override with env.
# 定价区分「缓存命中输入 / 未命中输入 / 输出」三档；未命中输入价 + 输出价见下表，
# 缓存命中输入价默认取未命中输入价 × DEFAULT_CACHE_RATIO（可用 env 精确覆盖）。
# 汇率按 7.14 折算：
#   V4-flash：1 / 2 元 → 0.14 / 0.28
#   V4-pro：  3 / 6 元 → 0.42 / 0.84
# deepseek-chat / deepseek-reasoner 为兼容别名，价格随官方调整。
_DEFAULT_PRICES: dict[str, tuple[float, float]] = {
    # model substring -> (prompt_per_m, completion_per_m)
    "deepseek-v4-flash": (0.14, 0.28),
    "deepseek-v4-pro": (0.42, 0.84),
    "deepseek-chat": (0.14, 0.28),
    "deepseek-reasoner": (0.55, 2.19),
    "default": (0.5, 1.5),
}

# 缓存命中输入价 = 未命中输入价 × 该比例（DeepSeek 缓存命中约 1/4，可 env 覆盖）。
DEFAULT_CACHE_RATIO: float = 0.25


def _parse_price_pair(raw: str) -> tuple[float, float] | None:
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) != 2:
        return None
    try:
        return float(parts[0]), float(parts[1])
    except ValueError:
        return None


def prices_for_model(model: str) -> tuple[float, float]:
    """Return (prompt_usd_per_1m, completion_usd_per_1m) for a model id."""
    # AGENT_LLM_PRICE_<MODEL>=prompt,completion  (model uppercased, - -> _)
    key = "AGENT_LLM_PRICE_" + (model or "default").upper().replace("-", "_").replace(".", "_")
    env_pair = os.getenv(key, "").strip()
    if env_pair:
        parsed = _parse_price_pair(env_pair)
        if parsed:
            return parsed
    global_pair = os.getenv("AGENT_LLM_PRICE_DEFAULT", "").strip()
    if global_pair:
        parsed = _parse_price_pair(global_pair)
        if parsed:
            return parsed
    m = (model or "").lower()
    for needle, pair in _DEFAULT_PRICES.items():
        if needle != "default" and needle in m:
            return pair
    return _DEFAULT_PRICES["default"]


def _cache_rate_for_model(model: str, prompt_rate: float) -> float:
    """缓存命中输入价（USD / 1M tokens）。

    覆盖优先级：
    1. ``AGENT_LLM_PRICE_CACHE_<MODEL>`` —— 精确指定命中价
    2. ``AGENT_LLM_PRICE_CACHE`` —— 全局指定命中价
    3. ``AGENT_LLM_PRICE_CACHE_RATIO`` —— 命中价 = 未命中价 × ratio
    4. ``DEFAULT_CACHE_RATIO``（0.25）
    """
    key = "AGENT_LLM_PRICE_CACHE_" + (model or "default").upper().replace("-", "_").replace(".", "_")
    for env_name in (key, "AGENT_LLM_PRICE_CACHE"):
        raw = os.getenv(env_name, "").strip()
        if raw:
            try:
                return float(raw)
            except ValueError:
                continue
    ratio_raw = os.getenv("AGENT_LLM_PRICE_CACHE_RATIO", "").strip()
    ratio = DEFAULT_CACHE_RATIO
    if ratio_raw:
        try:
            ratio = float(ratio_raw)
        except ValueError:
            ratio = DEFAULT_CACHE_RATIO
    return prompt_rate * ratio


def estimate_cost_usd(
    *,
    model: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    cached_tokens: int = 0,
) -> float:
    """Estimate USD cost, distinguishing cache-hit vs cache-miss input tokens.

    ``cached_tokens`` 为缓存命中的输入 token 数；该部分按缓存命中价计费，
    剩余输入 token 按未命中价计费。不传 ``cached_tokens`` 时行为与旧版一致
    （全部按未命中价）。
    """
    prompt_rate, completion_rate = prices_for_model(model)
    prompt = max(0, int(prompt_tokens))
    completion = max(0, int(completion_tokens))
    cached = min(max(0, int(cached_tokens)), prompt)
    miss = prompt - cached
    cache_rate = _cache_rate_for_model(model, prompt_rate)
    cost = (miss / 1_000_000.0) * prompt_rate + (
        cached / 1_000_000.0
    ) * cache_rate + (completion / 1_000_000.0) * completion_rate
    return round(cost, 6)


def enrich_usage(
    usage: dict[str, Any] | None,
    *,
    model: str,
) -> dict[str, Any] | None:
    """Attach model + cost_usd onto a usage dict (returns new dict)."""
    if not usage:
        return None
    out = dict(usage)
    out["model"] = model
    prompt = int(out.get("prompt_tokens") or 0)
    completion = int(out.get("completion_tokens") or 0)
    cached = int(out.get("cached_tokens") or 0)
    out["cost_usd"] = estimate_cost_usd(
        model=model,
        prompt_tokens=prompt,
        completion_tokens=completion,
        cached_tokens=cached,
    )
    return out
