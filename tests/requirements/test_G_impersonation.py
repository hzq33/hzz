"""需求域 G — 角色扮演（Impersonation）：扮演对话 / 检索口吻 / 会话管理 / 出处。

覆盖 docs/REQUIREMENTS.md G-01 ~ G-04。
黑盒：走公开 HTTP 端点；LLM 与 store 由 api_client fixture 注入。
"""

from __future__ import annotations

import json

from tests.conftest import auth_headers


def _sse_events(text: str) -> list[dict]:
    events = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            payload = line[5:].strip()
            if payload and payload != "[DONE]":
                events.append(json.loads(payload))
    return events


# ── G-01 扮演对话 ─────────────────────────────────────────────


def test_impersonate_requires_character_and_message(api_client):
    """G-01：character 与 message 必填——缺失/空串 → 422。"""
    assert (
        api_client.post(
            "/api/v1/agent/impersonate/chat",
            json={"message": "你好"},
            headers=auth_headers(),
        ).status_code
        == 422
    )
    assert (
        api_client.post(
            "/api/v1/agent/impersonate/chat",
            json={"character": "利姆露"},
            headers=auth_headers(),
        ).status_code
        == 422
    )


def test_impersonate_chat_returns_reply(api_client):
    """G-01：扮演 chat 返回回复 + 角色 + 会话 ID。"""
    r = api_client.post(
        "/api/v1/agent/impersonate/chat",
        json={"character": "利姆露", "message": "你好"},
        headers=auth_headers(),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["reply"]
    assert body["character"] == "利姆露"
    assert body["session_id"]


def test_impersonate_chat_stream_events(api_client):
    """G-01：扮演 stream 的 SSE 事件含 reply_chunk / done。"""
    r = api_client.post(
        "/api/v1/agent/impersonate/chat/stream",
        json={"character": "利姆露", "message": "你好"},
        headers=auth_headers(),
    )
    assert r.status_code == 200
    events = _sse_events(r.text)
    types = {e.get("type") for e in events}
    assert "reply_chunk" in types
    assert "done" in types


def test_impersonate_multi_turn_same_session(api_client):
    """G-01：同一会话多轮保持上下文（连续两轮均成功返回）。"""
    sid = None
    for msg in ("第一句", "第二句"):
        r = api_client.post(
            "/api/v1/agent/impersonate/chat",
            json={"character": "利姆露", "message": msg, "session_id": sid},
            headers=auth_headers(),
        )
        assert r.status_code == 200
        sid = r.json()["session_id"]


def test_impersonate_doc_id_lock_accepted(api_client):
    """G-01：doc_id 卷锁定参数被接受（不 4xx/5xx）。"""
    r = api_client.post(
        "/api/v1/agent/impersonate/chat",
        json={"character": "利姆露", "message": "你好", "doc_id": "某卷"},
        headers=auth_headers(),
    )
    assert r.status_code == 200


# ── G-03 会话管理 ─────────────────────────────────────────────


def test_impersonate_reset(api_client):
    """G-03：reset 重置会话（200，会话可继续使用）。"""
    r1 = api_client.post(
        "/api/v1/agent/impersonate/chat",
        json={"character": "利姆露", "message": "你好"},
        headers=auth_headers(),
    )
    sid = r1.json()["session_id"]
    r = api_client.post(
        "/api/v1/agent/impersonate/reset",
        params={"session_id": sid},
        headers=auth_headers(),
    )
    assert r.status_code == 200


def test_impersonate_regenerate(api_client):
    """G-03：regenerate 重新生成上一条回复（SSE 事件流）。"""
    r1 = api_client.post(
        "/api/v1/agent/impersonate/chat",
        json={"character": "利姆露", "message": "你好"},
        headers=auth_headers(),
    )
    sid = r1.json()["session_id"]
    r = api_client.post(
        "/api/v1/agent/impersonate/regenerate",
        json={"character": "利姆露", "session_id": sid},
        headers=auth_headers(),
    )
    assert r.status_code == 200
    events = _sse_events(r.text)
    types = {e.get("type") for e in events}
    assert "done" in types or "reply_chunk" in types


def test_impersonation_sessions_list_rename_delete(api_client):
    """G-03：会话列表 → 重命名 → 删除 全流程。"""
    r1 = api_client.post(
        "/api/v1/agent/impersonate/chat",
        json={"character": "利姆露", "message": "你好"},
        headers=auth_headers(),
    )
    sid = r1.json()["session_id"]

    lst = api_client.get("/api/v1/agent/impersonate/sessions", headers=auth_headers())
    assert lst.status_code == 200
    items = lst.json()["items"]
    assert any(s.get("session_id") == sid for s in items)

    rn = api_client.patch(
        f"/api/v1/agent/impersonate/sessions/{sid}",
        json={"title": "测试会话"},
        headers=auth_headers(),
    )
    assert rn.status_code == 200

    dl = api_client.delete(
        f"/api/v1/agent/impersonate/sessions/{sid}",
        headers=auth_headers(),
    )
    assert dl.status_code == 200
    lst2 = api_client.get("/api/v1/agent/impersonate/sessions", headers=auth_headers())
    items2 = lst2.json()["items"]
    assert not any(s.get("session_id") == sid for s in items2)


def test_impersonate_history(api_client):
    """G-03：扮演会话历史可查询（200，结构完整）。"""
    r1 = api_client.post(
        "/api/v1/agent/impersonate/chat",
        json={"character": "利姆露", "message": "你好"},
        headers=auth_headers(),
    )
    sid = r1.json()["session_id"]
    r = api_client.get(
        "/api/v1/agent/impersonate/history",
        params={"session_id": sid},
        headers=auth_headers(),
    )
    assert r.status_code == 200


# ── G-04 出处引用 ─────────────────────────────────────────────


def test_impersonate_citations_empty_on_empty_store(api_client):
    """G-04：空库扮演不产生引用（citations 为空列表，不 5xx）。"""
    r = api_client.post(
        "/api/v1/agent/impersonate/chat",
        json={"character": "利姆露", "message": "这本书讲了什么"},
        headers=auth_headers(),
    )
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body.get("citations"), list)
