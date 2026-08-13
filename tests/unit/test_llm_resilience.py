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
