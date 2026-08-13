"""Human-in-the-loop gate for high-risk tool calls."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.domain.novel.series_paths import data_root

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
        self._db_lock = threading.Lock()

    # ── SQLite 持久化（重启后审批记录不丢，多 worker 共享）──────────────

    def _db(self) -> sqlite3.Connection:
        p = Path(data_root()) / "approvals.db"
        p.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(p), timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS approvals ("
            " approval_id TEXT PRIMARY KEY,"
            " session_id TEXT NOT NULL DEFAULT '',"
            " tool_name TEXT NOT NULL,"
            " tool_args TEXT NOT NULL DEFAULT '{}',"
            " status TEXT NOT NULL DEFAULT 'pending',"
            " reason TEXT NOT NULL DEFAULT '',"
            " created_at REAL NOT NULL DEFAULT 0,"
            " updated_at REAL NOT NULL DEFAULT 0)"
        )
        return conn

    def _persist(self, rec: PendingApproval) -> None:
        try:
            with self._db_lock:
                conn = self._db()
                try:
                    conn.execute(
                        "INSERT OR REPLACE INTO approvals "
                        "(approval_id, session_id, tool_name, tool_args, status, reason, created_at, updated_at) "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        (
                            rec.approval_id,
                            rec.session_id,
                            rec.tool_name,
                            json.dumps(rec.tool_args, ensure_ascii=False),
                            rec.status,
                            rec.reason,
                            rec.created_at,
                            time.time(),
                        ),
                    )
                    conn.commit()
                finally:
                    conn.close()
        except Exception as exc:  # noqa: BLE001 - 持久化失败不阻塞审批主链路
            logger.warning("approval persist failed: %s", exc)

    def _load_db(self, approval_id: str) -> PendingApproval | None:
        try:
            with self._db_lock:
                conn = self._db()
                try:
                    row = conn.execute(
                        "SELECT approval_id, session_id, tool_name, tool_args, status, reason, created_at "
                        "FROM approvals WHERE approval_id = ?",
                        (approval_id,),
                    ).fetchone()
                finally:
                    conn.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("approval load failed: %s", exc)
            return None
        if row is None:
            return None
        rec = PendingApproval(
            approval_id=row[0],
            session_id=row[1],
            tool_name=row[2],
            tool_args=json.loads(row[3] or "{}"),
            created_at=row[6],
        )
        rec.status = row[4]
        rec.reason = row[5]
        return rec

    def expire_stale_pending(self) -> int:
        """重启后把 DB 里残留的 pending 标记 expired（进程内 Event 已丢失）。"""
        try:
            with self._db_lock:
                conn = self._db()
                try:
                    cur = conn.execute(
                        "UPDATE approvals SET status='expired', reason='expired (restart)', updated_at=? "
                        "WHERE status='pending'",
                        (time.time(),),
                    )
                    conn.commit()
                    return cur.rowcount
                finally:
                    conn.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("approval expire sweep failed: %s", exc)
            return 0

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
        self.cleanup_older_than(
            max_age_seconds=self._CLEANUP_INTERVAL, skip_decided=True
        )

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
        self._persist(rec)
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
                    self._persist(rec)
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
            self._persist(rec)
            return rec

    def get(self, approval_id: str) -> PendingApproval | None:
        rec = self._pending.get(approval_id)
        if rec is not None:
            return rec
        # 内存未命中则回退 DB（重启后内存丢失，历史审批仍可查）
        return self._load_db(approval_id)

    def cleanup_older_than(
        self, max_age_seconds: float = 3600.0, *, skip_decided: bool = False
    ) -> int:
        """Drop records older than ``max_age_seconds`` (synchronous, atomic).

        Runs without awaiting in the asyncio single-threaded loop, so the
        dict mutation cannot interleave with decide()/wait(). Called from
        ``_maybe_cleanup`` (expired sweep, ``skip_decided=True``) and from
        server startup maintenance (full sweep, reaping decided records too).

        ``skip_decided=True`` keeps approved/denied records: a ``wait()``
        coroutine may still be between ``event.wait()`` returning and reading
        ``self._pending`` — deleting the record in that window would make
        ``wait`` return False for an already-approved request.
        """
        cutoff = time.time() - max_age_seconds
        removed = 0
        for aid, rec in list(self._pending.items()):
            if rec.created_at < cutoff:
                if skip_decided and rec.status in {"approved", "denied"}:
                    continue
                self._pending.pop(aid, None)
                self._events.pop(aid, None)
                removed += 1
        # DB 同步清理（镜像维护；返回值仍以内存删除数为准，保持向后兼容）
        try:
            with self._db_lock:
                conn = self._db()
                try:
                    if skip_decided:
                        conn.execute(
                            "DELETE FROM approvals WHERE created_at < ? "
                            "AND status NOT IN ('approved','denied')",
                            (cutoff,),
                        )
                    else:
                        conn.execute(
                            "DELETE FROM approvals WHERE created_at < ?", (cutoff,)
                        )
                    conn.commit()
                finally:
                    conn.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("approval db cleanup failed: %s", exc)
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
    if emit is None:
        # 无 SSE 事件通道（CLI / 后台任务 / 扮演链路）时无法推送审批请求，
        # 直接拒绝而非挂起等待超时（否则会阻塞 hitl_timeout_seconds 秒）。
        return (
            f"Tool '{tool_name}' requires human approval, but no approval "
            f"channel (SSE) is available in this context. Denied automatically."
        )
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
