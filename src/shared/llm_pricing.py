"""Estimate USD cost from token usage (configurable via env)."""

from __future__ import annotations

import os
from typing import Any

# Default DeepSeek-ish list prices (USD / 1M tokens). Override with env.
_DEFAULT_PRICES: dict[str, tuple[float, float]] = {
    # model substring -> (prompt_per_m, completion_per_m)
    "deepseek-v4-flash": (0.14, 0.28),
    "deepseek-v4-pro": (0.55, 2.19),
    "deepseek-chat": (0.14, 0.28),
    "deepseek-reasoner": (0.55, 2.19),
    "default": (0.5, 1.5),
}


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


def estimate_cost_usd(
    *,
    model: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
) -> float:
    prompt_rate, completion_rate = prices_for_model(model)
    cost = (prompt_tokens / 1_000_000.0) * prompt_rate + (
        completion_tokens / 1_000_000.0
    ) * completion_rate
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
    out["cost_usd"] = estimate_cost_usd(
        model=model, prompt_tokens=prompt, completion_tokens=completion
    )
    return out
