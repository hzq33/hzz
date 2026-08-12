"""Human-in-the-loop gate for high-risk tool calls."""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("agent.hitl")


def hitl_enabled() -> bool:
    raw = os.getenv("AGENT_TOOL_HITL", "1").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def hitl_timeout_seconds() -> float:
    try:
        return max(5.0, float(os.getenv("AGENT_TOOL_HITL_TIMEOUT", "120")))
    except ValueError:
        return 120.0


def requires_approval(tool_name: str, args: dict[str, Any] | None = None) -> bool:
    """Return True for high-risk tools that must be approved when HITL is on."""
    if not hitl_enabled():
        return False
    name = (tool_name or "").strip()
    if name == "execute_code":
        return True
    if name == "file_operation":
        op = str((args or {}).get("operation") or "").lower()
        return op == "write"
    action = str((args or {}).get("action") or "").lower()
    if name == "novel_admin":
        return action in {"delete_volume", "purge_series", "rename_series", "redialogue"}
    if name == "character_kb":
        return action in {"build", "merge", "update"}
    if name == "story_analysis":
        return action == "build"
    return False


@dataclass
class PendingApproval:
    approval_id: str
    session_id: str
    tool_name: str
    tool_args: dict[str, Any]
    created_at: float = field(default_factory=time.time)
    status: str = "pending"  # pending|approved|denied|expired
    reason: str = ""

    def to_public_dict(self) -> dict[str, Any]:
        args = dict(self.tool_args or {})
        # Avoid dumping huge code blobs in SSE
        if "code" in args and isinstance(args["code"], str) and len(args["code"]) > 400:
            args["code"] = args["code"][:400] + "…[truncated]"
        if "content" in args and isinstance(args["content"], str) and len(args["content"]) > 400:
            args["content"] = args["content"][:400] + "…[truncated]"
        return {
            "approval_id": self.approval_id,
            "session_id": self.session_id,
            "tool_name": self.tool_name,
            "tool_args": args,
            "status": self.status,
            "created_at": self.created_at,
            "timeout_seconds": hitl_timeout_seconds(),
        }


class ToolApprovalService:
    """Process-local pending approvals with asyncio waiters."""

    def __init__(self) -> None:
        self._pending: dict[str, PendingApproval] = {}
        self._events: dict[str, asyncio.Event] = {}
        self._lock = asyncio.Lock()
        self._last_cleanup = 0.0

    async def _maybe_cleanup(self) -> None:
        """Periodically drop expired records (rate-limited).

        Called on every request; the actual sweep runs at most once per
        ``_CLEANUP_INTERVAL`` — or immediately when ``_pending`` grows past
        ``_MAX_PENDING`` (so a burst of requests cannot balloon memory
        between sweeps).

        Decided (approved/denied) records are intentionally NOT dropped here:
        a ``wait()`` coroutine may still be between ``event.wait()`` returning
        and reading ``self._pending`` — deleting the record in that window made
        ``wait`` return False for an already-approved request. The 1h
        ``cleanup_older_than`` sweep reaps them instead (records are tiny).
        """
        now = time.time()
        if (
            now - self._last_cleanup < self._CLEANUP_INTERVAL
            and len(self._pending) < self._MAX_PENDING
        ):
            return
        self._last_cleanup = now
        self.cleanup_older_than(max_age_seconds=self._CLEANUP_INTERVAL)

    # Sweep interval (seconds) — expired records are reaped at most this often.
    _CLEANUP_INTERVAL: float = 300.0
    # Hard cap on pending records before a forced sweep.
    _MAX_PENDING: int = 500

    async def request(
        self,
        *,
        session_id: str,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> PendingApproval:
        await self._maybe_cleanup()
        aid = f"appr_{uuid.uuid4().hex[:12]}"
        rec = PendingApproval(
            approval_id=aid,
            session_id=session_id or "",
            tool_name=tool_name,
            tool_args=dict(tool_args or {}),
        )
        async with self._lock:
            self._pending[aid] = rec
            self._events[aid] = asyncio.Event()
        return rec

    async def wait(self, approval_id: str, timeout: float | None = None) -> bool:
        """Block until approved/denied/expired. Returns True iff approved."""
        timeout = hitl_timeout_seconds() if timeout is None else timeout
        event = self._events.get(approval_id)
        if event is None:
            return False
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except TimeoutError:
            async with self._lock:
                rec = self._pending.get(approval_id)
                if rec and rec.status == "pending":
                    rec.status = "expired"
                    rec.reason = "timeout"
            return False
        rec = self._pending.get(approval_id)
        return bool(rec and rec.status == "approved")

    async def decide(
        self,
        approval_id: str,
        *,
        approved: bool,
        reason: str = "",
    ) -> PendingApproval | None:
        async with self._lock:
            rec = self._pending.get(approval_id)
            if rec is None:
                return None
            if rec.status != "pending":
                return rec
            rec.status = "approved" if approved else "denied"
            rec.reason = reason or ("approved" if approved else "denied")
            ev = self._events.get(approval_id)
            if ev:
                ev.set()
            return rec

    def get(self, approval_id: str) -> PendingApproval | None:
        return self._pending.get(approval_id)

    def cleanup_older_than(self, max_age_seconds: float = 3600.0) -> int:
        """Drop records older than ``max_age_seconds`` (synchronous, atomic).

        Runs without awaiting in the asyncio single-threaded loop, so the
        dict mutation cannot interleave with decide()/wait(). Called from
        ``_maybe_cleanup`` and from server startup maintenance.
        """
        cutoff = time.time() - max_age_seconds
        removed = 0
        for aid, rec in list(self._pending.items()):
            if rec.created_at < cutoff:
                self._pending.pop(aid, None)
                self._events.pop(aid, None)
                removed += 1
        return removed


_SERVICE: ToolApprovalService | None = None


def get_approval_service() -> ToolApprovalService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = ToolApprovalService()
    return _SERVICE


async def gate_tool_execution(
    *,
    session_id: str,
    tool_name: str,
    tool_args: dict[str, Any],
    emit: Any | None = None,
) -> str | None:
    """If HITL required, emit approval_required and wait.

    Returns an error string when denied/expired; None when allowed to run.
    ``emit`` is an optional async callable(event_type, data_dict).
    """
    if not requires_approval(tool_name, tool_args):
        return None
    svc = get_approval_service()
    pending = await svc.request(
        session_id=session_id, tool_name=tool_name, tool_args=tool_args
    )
    logger.info(
        "HITL required tool=%s approval_id=%s session=%s",
        tool_name,
        pending.approval_id,
        session_id,
    )
    if emit is not None:
        await emit("approval_required", pending.to_public_dict())
    ok = await svc.wait(pending.approval_id)
    if ok:
        return None
    status = (svc.get(pending.approval_id) or pending).status
    return f"Tool '{tool_name}' {status} by human approval gate (id={pending.approval_id})."
