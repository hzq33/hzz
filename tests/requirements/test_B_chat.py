"""需求域 B — 通用对话：chat / stream / 工具列表 / 会话预算 / HITL。

覆盖 docs/REQUIREMENTS.md B-01 ~ B-04。
黑盒：走公开 HTTP 端点；LLM 由 api_client fixture 注入 FakeLLM。
"""

from __future__ import annotations

import json

import pytest

from tests.conftest import auth_headers


def _sse_events(text: str) -> list[dict]:
    """解析 SSE 文本为事件 dict 列表（data: JSON 行）。"""
    events = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            payload = line[5:].strip()
            if payload and payload != "[DONE]":
                events.append(json.loads(payload))
    return events


# ── B-01 chat 与 chat/stream ──────────────────────────────────


def test_chat_requires_message(api_client):
    """B-01：message 必填——缺失/空串 → 422。"""
    assert api_client.post("/api/v1/agent/chat", json={}, headers=auth_headers()).status_code == 422
    assert (
        api_client.post(
            "/api/v1/agent/chat", json={"message": ""}, headers=auth_headers()
        ).status_code
        == 422
    )


def test_chat_returns_reply_and_session(api_client):
    """B-01：chat 返回助手回复与会话 ID（未传时自动生成）。"""
    r = api_client.post(
        "/api/v1/agent/chat",
        json={"message": "你好"},
        headers=auth_headers(),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["reply"]
    assert body["session_id"]


def test_chat_stream_emits_expected_events(api_client):
    """B-01：chat/stream 的 SSE 事件流含 phase / reply_chunk / done（带 usage）。"""
    r = api_client.post(
        "/api/v1/agent/chat/stream",
        json={"message": "你好"},
        headers=auth_headers(),
    )
    assert r.status_code == 200
    events = _sse_events(r.text)
    types = {e.get("type") for e in events}
    assert "reply_chunk" in types
    assert "done" in types
    done = next(e for e in events if e.get("type") == "done")
    assert done.get("session_id")
    # usage/cost 来自 LLM 响应；有 usage 时必须是结构化对象
    if done.get("usage") is not None:
        assert isinstance(done["usage"], dict)


def test_chat_same_session_shares_context(api_client):
    """B-01：同一 session_id 多轮共享上下文（LLM 收到历史消息）。"""
    llm = api_client.app.state._test_llm
    sid = "ctx-test"
    api_client.post(
        "/api/v1/agent/chat",
        json={"message": "第一轮", "session_id": sid},
        headers=auth_headers(),
    )
    first_call_msgs = llm.last_messages
    api_client.post(
        "/api/v1/agent/chat",
        json={"message": "第二轮", "session_id": sid},
        headers=auth_headers(),
    )
    assert len(llm.last_messages) > len(first_call_msgs)


def test_chat_novel_scope_accepted(api_client):
    """B-01：novel_scope 可选参数被接受（不 4xx/5xx）。"""
    r = api_client.post(
        "/api/v1/agent/chat",
        json={"message": "查一下", "novel_scope": {"series_id": "某系列"}},
        headers=auth_headers(),
    )
    assert r.status_code == 200


# ── B-02 工具列表 ─────────────────────────────────────────────


def test_tools_list_contains_core_tools(api_client):
    """B-02：工具列表包含核心内置工具。"""
    r = api_client.get("/api/v1/agent/tools", headers=auth_headers())
    assert r.status_code == 200
    names = {t["name"] for t in r.json()}
    assert {
        "web_search",
        "novel_search",
        "character_kb",
        "story_analysis",
        "novel_admin",
        "file_operation",
    }.issubset(names)


# ── B-03 会话预算 ─────────────────────────────────────────────


def test_budget_exceeded_emits_error_event(api_client, monkeypatch):
    """B-03：会话 token 预算超限时 stream 以 error(budget) 结束。"""
    monkeypatch.setenv("AGENT_SESSION_TOKEN_BUDGET", "10")
    from src.shared.session_budget import add_session_tokens, reset_session

    sid = "budget-test"
    reset_session(sid)
    add_session_tokens(sid, 10)
    try:
        r = api_client.post(
            "/api/v1/agent/chat/stream",
            json={"message": "你好", "session_id": sid},
            headers=auth_headers(),
        )
        events = _sse_events(r.text)
        error_events = [e for e in events if e.get("type") == "error"]
        assert error_events, "应输出 error 事件"
        assert any(
            e.get("phase") == "budget" for e in error_events
        ), "error 事件应标记 budget 阶段"
    finally:
        reset_session(sid)


def test_budget_reset_allows_new_usage(api_client, monkeypatch):
    """B-03：会话重置后预算计数清零，不再报超限。"""
    monkeypatch.setenv("AGENT_SESSION_TOKEN_BUDGET", "10")
    from src.shared.session_budget import add_session_tokens, check_budget, reset_session

    sid = "budget-reset-test"
    reset_session(sid)
    add_session_tokens(sid, 10)
    assert check_budget(sid) is not None
    reset_session(sid)
    assert check_budget(sid) is None


# ── B-04 工具调用与 HITL ──────────────────────────────────────


def test_approve_unknown_approval_404(api_client):
    """B-04：审批不存在的 approval id → 404（不 500）。"""
    r = api_client.post(
        "/api/v1/agent/tools/approve",
        json={"approval_id": "no-such-approval", "approved": True},
        headers=auth_headers(),
    )
    assert r.status_code == 404


# ── B 补充：web_search 可测试后端 ─────────────────────────────


async def test_mock_search_provider_returns_results():
    """B：web_search 的 mock 后端确定性返回语料结果（测试/离线可用）。"""
    from src.infrastructure.search_provider import MockSearchProvider

    provider = MockSearchProvider()
    results = await provider.search("large language models")
    assert results
    assert all(r.get("title") and r.get("url") for r in results)
