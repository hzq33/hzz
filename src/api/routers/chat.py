"""General agent chat and history routes."""

from __future__ import annotations

import logging
from contextlib import contextmanager

from fastapi import APIRouter, HTTPException, Query, Request
from sse_starlette.event import JSONServerSentEvent
from sse_starlette.sse import EventSourceResponse

from src.api.errors import raise_internal_error
from src.api.schemas import ChatRequest, ChatResponse
from src.shared.request_context import reset_session_id, set_session_id

logger = logging.getLogger("agent_server")
router = APIRouter(prefix="/api/v1/agent")


@contextmanager
def _bind_session_context(session_id: str | None):
    """Bind current request's session id for RAG trace attribution."""
    token = set_session_id(session_id)
    try:
        yield
    finally:
        reset_session_id(token)


@router.get("/tools")
async def list_tools(request: Request):
    try:
        return request.app.state.get_tools_info()
    except HTTPException:
        return []


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, request: Request):
    conversation = request.app.state.conversation
    async with conversation.locked_session(
        req.session_id, request.app.state.load_config
    ) as session:
        if session is None:
            raise HTTPException(
                status_code=503,
                detail="Agent not configured: set DEEPSEEK_API_KEY in .env or environment",
            )
        try:
            agent = session["agent"]
            streaming = session["streaming"]
            reply_parts: list[str] = []
            with _bind_session_context(session["session_id"]):
                async for event in streaming.run_stream(
                    req.message, novel_scope=req.novel_scope
                ):
                    if event.type == "reply_chunk":
                        token = event.data.get("token") or event.data.get("content") or ""
                        if token:
                            reply_parts.append(token)
                    elif event.type == "error":
                        raise HTTPException(
                            status_code=500,
                            detail=event.data.get("message", "stream error"),
                        )
            reply = "".join(reply_parts)
            conversation.persist(session["session_id"], agent)
            return ChatResponse(reply=reply, session_id=session["session_id"])
        except HTTPException:
            raise
        except Exception as exc:
            raise_internal_error(exc, public_detail="Chat request failed", log_message="Chat error")


@router.post("/chat/stream")
async def chat_stream(req: ChatRequest, request: Request):
    conversation = request.app.state.conversation

    async def event_generator():
        from src.shared.session_budget import check_budget, record_usage_dict

        budget_err = check_budget(req.session_id)
        if budget_err:
            yield JSONServerSentEvent(
                {"type": "error", "phase": "budget", "message": budget_err}
            )
            return

        async with conversation.locked_session(
            req.session_id, request.app.state.load_config
        ) as session:
            if session is None:
                yield JSONServerSentEvent(
                    {
                        "type": "error",
                        "message": (
                            "Agent not configured: set DEEPSEEK_API_KEY "
                            "in .env or environment"
                        ),
                    }
                )
                return
            streaming = session["streaming"]
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
                    async for event in streaming.run_stream(
                        req.message, novel_scope=req.novel_scope
                    ):
                        if await request.is_disconnected():
                            logger.info(
                                "Chat SSE client disconnected; cancelling graph session_id=%s",
                                sid,
                            )
                            break
                        payload = {"type": event.type, **event.data}
                        if event.type == "done":
                            usage = event.data.get("usage")
                            record_usage_dict(sid, usage)
                            if usage is not None:
                                payload["usage"] = usage
                        yield JSONServerSentEvent(payload)
            except Exception as exc:
                logger.exception("Stream error")
                yield JSONServerSentEvent(
                    {
                        "type": "error",
                        "message": "Internal error while streaming. Check server logs for details.",
                    }
                )
            finally:
                conversation.persist(session["session_id"], agent)

    return EventSourceResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/history")
async def get_history(
    request: Request,
    session_id: str = Query(..., description="Session ID"),
):
    conversation = request.app.state.conversation
    session = await conversation.get_or_create(
        session_id, request.app.state.load_config
    )
    if session is None:
        raise HTTPException(status_code=503, detail="Agent not configured")

    messages = session["agent"].memory.get_messages()
    return {
        "session_id": session_id,
        "messages": [
            {"role": message.get("role", "unknown"), "content": message.get("content", "")}
            for message in messages
            if message.get("role") in ("user", "assistant")
        ],
    }


@router.delete("/history")
async def clear_history(
    request: Request,
    session_id: str = Query(..., description="Session ID"),
):
    from src.shared.session_budget import reset_session

    cleared = request.app.state.conversation.clear(session_id)
    reset_session(session_id)
    return {
        "message": "History cleared" if cleared else "Session not found",
        "session_id": session_id,
    }
