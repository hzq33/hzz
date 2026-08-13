"""Unit tests for LLM cost estimation (cache-hit vs cache-miss input tokens)."""

from types import SimpleNamespace

import pytest

from src.shared.llm_pricing import enrich_usage, estimate_cost_usd
from src.shared.llm_resilience import _extract_cached_tokens


class TestEstimateCostUsd:
    # deepseek-v4-flash: 未命中输入 0.14 / 输出 0.28（USD / 1M），缓存比例默认 0.25

    def test_no_cached_matches_legacy(self, monkeypatch):
        monkeypatch.delenv("AGENT_LLM_PRICE_CACHE_RATIO", raising=False)
        cost = estimate_cost_usd(
            model="deepseek-v4-flash",
            prompt_tokens=1_000_000,
            completion_tokens=1_000_000,
        )
        assert cost == pytest.approx(0.42)  # 0.14 + 0.28

    def test_fully_cached_input_cheaper(self, monkeypatch):
        monkeypatch.delenv("AGENT_LLM_PRICE_CACHE_RATIO", raising=False)
        cost = estimate_cost_usd(
            model="deepseek-v4-flash",
            prompt_tokens=1_000_000,
            completion_tokens=0,
            cached_tokens=1_000_000,
        )
        assert cost == pytest.approx(0.14 * 0.25)  # 0.035

    def test_partial_cache_split(self, monkeypatch):
        monkeypatch.delenv("AGENT_LLM_PRICE_CACHE_RATIO", raising=False)
        cost = estimate_cost_usd(
            model="deepseek-v4-flash",
            prompt_tokens=1_000_000,
            completion_tokens=1_000_000,
            cached_tokens=500_000,
        )
        expected = 0.5 * 0.14 + 0.5 * (0.14 * 0.25) + 0.28
        assert cost == pytest.approx(expected)

    def test_cached_capped_at_prompt(self, monkeypatch):
        monkeypatch.delenv("AGENT_LLM_PRICE_CACHE_RATIO", raising=False)
        cost = estimate_cost_usd(
            model="deepseek-v4-flash",
            prompt_tokens=1_000_000,
            cached_tokens=2_000_000,
        )
        # cached 截断为 prompt，全部按命中价
        assert cost == pytest.approx(0.14 * 0.25)

    def test_ratio_env_override(self, monkeypatch):
        monkeypatch.setenv("AGENT_LLM_PRICE_CACHE_RATIO", "0.5")
        cost = estimate_cost_usd(
            model="deepseek-v4-flash",
            prompt_tokens=1_000_000,
            cached_tokens=1_000_000,
        )
        assert cost == pytest.approx(0.14 * 0.5)

    def test_exact_price_env_override(self, monkeypatch):
        monkeypatch.setenv("AGENT_LLM_PRICE_CACHE_DEEPSEEK_V4_FLASH", "0.01")
        cost = estimate_cost_usd(
            model="deepseek-v4-flash",
            prompt_tokens=1_000_000,
            cached_tokens=1_000_000,
        )
        assert cost == pytest.approx(0.01)


class TestEnrichUsage:
    def test_attaches_model_and_cache_aware_cost(self):
        out = enrich_usage(
            {
                "prompt_tokens": 1_000_000,
                "completion_tokens": 0,
                "cached_tokens": 1_000_000,
            },
            model="deepseek-v4-flash",
        )
        assert out["model"] == "deepseek-v4-flash"
        assert out["cost_usd"] == pytest.approx(0.14 * 0.25)

    def test_without_cached_tokens_legacy_cost(self):
        out = enrich_usage(
            {"prompt_tokens": 1_000_000, "completion_tokens": 0},
            model="deepseek-v4-flash",
        )
        assert out["cost_usd"] == pytest.approx(0.14)

    def test_none_usage(self):
        assert enrich_usage(None, model="x") is None


class TestExtractCachedTokens:
    def test_deepseek_field(self):
        u = SimpleNamespace(prompt_cache_hit_tokens=123)
        assert _extract_cached_tokens(u) == 123

    def test_openai_compat_field(self):
        u = SimpleNamespace(prompt_tokens_details=SimpleNamespace(cached_tokens=45))
        assert _extract_cached_tokens(u) == 45

    def test_missing_returns_zero(self):
        assert _extract_cached_tokens(SimpleNamespace()) == 0

    def test_none_returns_zero(self):
        assert _extract_cached_tokens(None) == 0
