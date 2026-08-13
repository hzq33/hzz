"""Character impersonation chat routes."""

from __future__ import annotations

import logging
from contextlib import contextmanager

from fastapi import APIRouter, HTTPException, Query, Request
from sse_starlette.event import JSONServerSentEvent
from sse_starlette.sse import EventSourceResponse

from src.api.errors import raise_internal_error
from src.api.schemas import (
    ImpersonateRegenerateRequest,
    ImpersonateRequest,
    ImpersonateResponse,
)
from src.shared.request_context import reset_session_id, set_session_id

logger = logging.getLogger("agent_server")
router = APIRouter(prefix="/api/v1/agent/impersonate")


@contextmanager
def _bind_session_context(session_id: str | None):
    """Bind current request's session id for RAG trace attribution."""
    token = set_session_id(session_id)
    try:
        yield
    finally:
        reset_session_id(token)


async def _locked_session(request: Request, req: ImpersonateRequest):
    store = await request.app.state.get_imp_store()
    config = request.app.state.require_config()
    return request.app.state.imp_sessions.locked_session(
        req.character,
        req.session_id,
        doc_id=req.doc_id,
        store=store,
        config=config,
        llm_factory=request.app.state.create_shared_llm,
    )


@router.post("/chat", response_model=ImpersonateResponse)
async def impersonate_chat(req: ImpersonateRequest, request: Request):
    try:
        context = await _locked_session(request, req)
        async with context as session:
            agent = session["agent"]
            with _bind_session_context(session["session_id"]):
                reply = await agent.chat(req.message)
                citations = []
                if hasattr(agent, "_citations_event_payload"):
                    citations = agent._citations_event_payload().get("items") or []
                elif hasattr(agent, "get_last_citations"):
                    citations = [
                        citation.to_evidence() for citation in agent.get_last_citations()
                    ]
            request.app.state.imp_sessions.persist(session["session_id"])
            mem = getattr(agent, "memory", None)
            memory_stats = None
            if mem is not None:
                memory_stats = {
                    "max_tokens": getattr(mem, "max_tokens", None),
                    "tokens_est": mem.estimate_tokens(),
                    "summarized_turns": mem.get_summarized_turns(),
                    "summary_excerpt": (mem.get_summary() or "")[:200],
                }
            return ImpersonateResponse(
                reply=reply,
                character=req.character,
                session_id=session["session_id"],
                citations=citations,
                memory_stats=memory_stats,
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise_internal_error(
            exc,
            public_detail="Impersonation chat failed",
            log_message="Impersonation chat error",
        )


@router.post("/chat/stream")
async def impersonate_chat_stream(req: ImpersonateRequest, request: Request):
    async def event_generator():
        from src.shared.session_budget import check_budget, record_usage_dict

        budget_err = check_budget(req.session_id)
        if budget_err:
            yield JSONServerSentEvent(
                {"type": "error", "phase": "budget", "message": budget_err}
            )
            return
        try:
            context = await _locked_session(request, req)
            async with context as session:
                agent = session["agent"]
                sid = session["session_id"]
                try:
                    budget_err = check_budget(sid)
                    if budget_err:
                        yield JSONServerSentEvent(
                            {"type": "error", "phase": "budget", "message": budget_err}
                        )
                        return
                    with _bind_session_context(sid):
                        if hasattr(agent, "iter_chat_events"):
                            async for event in agent.iter_chat_events(req.message):
                                if await request.is_disconnected():
                                    logger.info(
                                        "Impersonation SSE disconnected session_id=%s",
                                        sid,
                                    )
                                    break
                                yield JSONServerSentEvent(event)
                        else:
                            async for token in agent.chat_stream(req.message):
                                if await request.is_disconnected():
                                    logger.info(
                                        "Impersonation SSE disconnected session_id=%s",
                                        sid,
                                    )
                                    break
                                yield JSONServerSentEvent(
                                    {"type": "reply_chunk", "token": token}
                                )
                    usage = getattr(
                        getattr(agent, "_llm", None), "last_usage", None
                    )
                    record_usage_dict(sid, usage)
                    mem = getattr(agent, "memory", None)
                    memory_stats = None
                    if mem is not None:
                        memory_stats = {
                            "max_tokens": getattr(mem, "max_tokens", None),
                            "tokens_est": mem.estimate_tokens(),
                            "summarized_turns": mem.get_summarized_turns(),
                            "summary_excerpt": (mem.get_summary() or "")[:200],
                        }
                    yield JSONServerSentEvent(
                        {
                            "type": "done",
                            "character": req.character,
                            "session_id": sid,
                            "max_history_tokens": getattr(
                                agent, "max_history_tokens", None
                            ),
                            "memory_stats": memory_stats,
                            "usage": usage,
                        }
                    )
                finally:
                    request.app.state.imp_sessions.persist(session["session_id"])
        except Exception:
            logger.exception("Impersonation stream error")
            yield JSONServerSentEvent(
                {
                    "type": "error",
                    "message": "Internal error while streaming. Check server logs for details.",
                }
            )

    return EventSourceResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/reset")
async def impersonate_reset(
    request: Request,
    session_id: str = Query(..., description="Session ID"),
):
    service = request.app.state.imp_sessions
    try:
        async with service.session_lock(session_id):
            reset = service.reset(session_id)
    except ValueError:
        reset = False
    return {
        "message": "Session reset" if reset else "Session not found",
        "session_id": session_id,
    }


@router.post("/regenerate")
async def impersonate_regenerate(
    req: ImpersonateRegenerateRequest, request: Request
):
    service = request.app.state.imp_sessions

    async def event_generator():
        try:
            async with service.session_lock(req.session_id):
                session = service.get(req.session_id)
                if session is None:
                    yield JSONServerSentEvent(
                        {"type": "error", "message": "Session not found"}
                    )
                    return
                if session["character"] != req.character:
                    yield JSONServerSentEvent(
                        {
                            "type": "error",
                            "message": "Character mismatch for session",
                        }
                    )
                    return

                agent = session["agent"]
                if hasattr(agent, "set_doc_id"):
                    agent.set_doc_id(req.doc_id)
                session["doc_id"] = req.doc_id
                agent.pop_last_assistant()
                last_user: str | None = None
                for message in reversed(agent.get_history()):
                    if message.get("role") == "user":
                        last_user = message.get("content") or ""
                        break
                if not last_user:
                    yield JSONServerSentEvent(
                        {
                            "type": "error",
                            "message": "No user message to regenerate from",
                        }
                    )
                    return
                agent.pop_last_user()

                try:
                    from src.shared.session_budget import check_budget, record_usage_dict

                    budget_err = check_budget(req.session_id)
                    if budget_err:
                        yield JSONServerSentEvent(
                            {
                                "type": "error",
                                "phase": "budget",
                                "message": budget_err,
                            }
                        )
                        return
                    with _bind_session_context(req.session_id):
                        if hasattr(agent, "iter_chat_events"):
                            async for event in agent.iter_chat_events(last_user):
                                if await request.is_disconnected():
                                    logger.info(
                                        "Impersonation regenerate SSE disconnected session_id=%s",
                                        req.session_id,
                                    )
                                    break
                                yield JSONServerSentEvent(event)
                        else:
                            async for token in agent.chat_stream(last_user):
                                if await request.is_disconnected():
                                    logger.info(
                                        "Impersonation regenerate SSE disconnected session_id=%s",
                                        req.session_id,
                                    )
                                    break
                                yield JSONServerSentEvent(
                                    {"type": "reply_chunk", "token": token}
                                )
                    usage = getattr(
                        getattr(agent, "_llm", None), "last_usage", None
                    )
                    record_usage_dict(req.session_id, usage)
                    yield JSONServerSentEvent(
                        {
                            "type": "done",
                            "character": req.character,
                            "session_id": req.session_id,
                            "max_history_tokens": getattr(
                                agent, "max_history_tokens", None
                            ),
                            "usage": usage,
                        }
                    )
                finally:
                    service.persist(req.session_id)
        except Exception:
            logger.exception("Impersonation regenerate error")
            yield JSONServerSentEvent(
                {
                    "type": "error",
                    "message": "Internal error while regenerating. Check server logs for details.",
                }
            )

    session = service.get(req.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if session["character"] != req.character:
        raise HTTPException(status_code=400, detail="Character mismatch for session")

    return EventSourceResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/sessions")
async def list_impersonation_sessions(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
):
    return {
        "items": request.app.state.imp_sessions.list_summaries(limit=limit),
    }


@router.patch("/sessions/{session_id}")
async def rename_impersonation_session(
    session_id: str,
    request: Request,
):
    body = await request.json()
    title = (body or {}).get("title") if isinstance(body, dict) else None
    if not isinstance(title, str) or not title.strip():
        raise HTTPException(status_code=400, detail="title required")
    try:
        ok = request.app.state.imp_sessions.update_title(session_id, title)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc
    if not ok:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"session_id": session_id, "title": title.strip()[:80]}


@router.delete("/sessions/{session_id}")
async def delete_impersonation_session(session_id: str, request: Request):
    try:
        ok = request.app.state.imp_sessions.delete_session(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc
    if not ok:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"message": "Session deleted", "session_id": session_id}


@router.get("/history")
async def impersonate_history(
    request: Request,
    session_id: str = Query(..., description="Session ID"),
):
    try:
        history = request.app.state.imp_sessions.load_history(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc
    if history is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return history
