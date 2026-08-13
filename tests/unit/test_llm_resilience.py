"""Unit tests for LLMResilienceMixin retry classification & fallback switch."""

import httpx
import pytest
from openai import APIStatusError, RateLimitError

from src.shared.llm import SharedLLMClient
from src.shared.llm_resilience import LLMResilienceMixin


def _api_status(status_code: int) -> APIStatusError:
    req = httpx.Request("POST", "https://api.deepseek.com")
    resp = httpx.Response(status_code, request=req)
    return APIStatusError("test error", response=resp, body=None)


def _rate_limit() -> RateLimitError:
    req = httpx.Request("POST", "https://api.deepseek.com")
    resp = httpx.Response(429, request=req)
    return RateLimitError("rate limited", response=resp, body=None)


class TestIsRetryable:
    def test_4xx_not_retryable(self):
        assert LLMResilienceMixin._is_retryable(_api_status(400)) is False
        assert LLMResilienceMixin._is_retryable(_api_status(401)) is False
        assert LLMResilienceMixin._is_retryable(_api_status(404)) is False

    def test_5xx_retryable(self):
        assert LLMResilienceMixin._is_retryable(_api_status(500)) is True
        assert LLMResilienceMixin._is_retryable(_api_status(503)) is True

    def test_rate_limit_retryable(self):
        assert LLMResilienceMixin._is_retryable(_rate_limit()) is True

    def test_timeout_connection_retryable(self):
        assert LLMResilienceMixin._is_retryable(TimeoutError()) is True
        assert LLMResilienceMixin._is_retryable(ConnectionError()) is True

    def test_other_exception_not_retryable(self):
        assert LLMResilienceMixin._is_retryable(ValueError("x")) is False
        assert LLMResilienceMixin._is_retryable(RuntimeError("x")) is False


class TestFallbackSwitch:
    def test_switch_on_consecutive_failures(self):
        client = SharedLLMClient(
            primary={
                "api_key": "k",
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-v4-flash",
            },
            fallback={
                "api_key": "k",
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-v4-pro",
            },
        )
        # 第 1 次失败：未达阈值，不切换
        assert client._on_failure(RuntimeError("boom")) is False
        assert client.is_using_fallback() is False
        # 第 2 次失败：达到 fallback_failure_threshold(2)，切换并提示重试
        assert client._on_failure(RuntimeError("boom")) is True
        assert client.is_using_fallback() is True

    def test_no_fallback_configured(self):
        client = SharedLLMClient(
            primary={
                "api_key": "k",
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-v4-flash",
            },
        )
        # 无 fallback 时永不切换
        assert client._on_failure(RuntimeError("boom")) is False
        assert client._on_failure(RuntimeError("boom")) is False
        assert client.is_using_fallback() is False


class TestBackoffSeconds:
    def test_exponential_no_jitter(self, monkeypatch):
        monkeypatch.setenv("AGENT_LLM_RETRY_JITTER", "0")
        client = SharedLLMClient(
            primary={
                "api_key": "k",
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-v4-flash",
            },
        )
        assert client._backoff_seconds(0) == 1.0
        assert client._backoff_seconds(1) == 2.0
        assert client._backoff_seconds(2) == 4.0
        assert client._backoff_seconds(3) == 4.0  # 封顶 4

    def test_retry_after_header(self, monkeypatch):
        monkeypatch.setenv("AGENT_LLM_RETRY_JITTER", "0")
        client = SharedLLMClient(
            primary={
                "api_key": "k",
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-v4-flash",
            },
        )
        req = httpx.Request("POST", "https://api.deepseek.com")
        resp = httpx.Response(500, request=req, headers={"Retry-After": "7"})
        exc = APIStatusError("err", response=resp, body=None)
        assert client._backoff_seconds(0, exc) == 7.0

    def test_jitter_range(self, monkeypatch):
        monkeypatch.setenv("AGENT_LLM_RETRY_JITTER", "1")
        client = SharedLLMClient(
            primary={
                "api_key": "k",
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-v4-flash",
            },
        )
        for _ in range(20):
            v = client._backoff_seconds(0)
            assert 0.5 <= v <= 1.5


class TestTryRevert:
    def _client_in_fallback(self):
        import time

        client = SharedLLMClient(
            primary={
                "api_key": "k",
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-v4-flash",
            },
            fallback={
                "api_key": "k",
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-v4-pro",
            },
        )
        client._using_fallback = True
        client._fallback_since = time.time() - 100  # 超过 probe backoff
        return client

    def test_probe_success_reverts(self, monkeypatch):
        client = self._client_in_fallback()

        class _FakeModels:
            def list(self):
                return []

        class _FakeOpenAI:
            def __init__(self, **kwargs):
                pass

            @property
            def models(self):
                return _FakeModels()

        monkeypatch.setattr("src.shared.llm_resilience.OpenAI", _FakeOpenAI)
        client._try_revert()
        assert client._using_fallback is False
        assert client._fallback_since is None

    def test_probe_failure_stays_on_fallback(self, monkeypatch):
        client = self._client_in_fallback()

        class _FakeOpenAI:
            def __init__(self, **kwargs):
                pass

            @property
            def models(self):
                raise RuntimeError("probe failed")

        monkeypatch.setattr("src.shared.llm_resilience.OpenAI", _FakeOpenAI)
        client._try_revert()
        assert client._using_fallback is True
        assert client._probe_failures == 1
