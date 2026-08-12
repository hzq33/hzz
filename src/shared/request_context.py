"""Request-scoped context (request id) for logging and tracing."""

from __future__ import annotations

from contextvars import ContextVar, Token

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
# 当前请求归属的会话（通用助手或角色扮演），供 RAG trace 记录会话归属。
_session_id: ContextVar[str | None] = ContextVar("session_id", default=None)


def get_request_id() -> str | None:
    return _request_id.get()


def set_request_id(request_id: str) -> Token:
    return _request_id.set(request_id)


def reset_request_id(token: Token) -> None:
    _request_id.reset(token)


def get_session_id() -> str | None:
    return _session_id.get()


def set_session_id(session_id: str | None) -> Token:
    return _session_id.set(session_id)


def reset_session_id(token: Token) -> None:
    _session_id.reset(token)
