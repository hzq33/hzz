"""Per-session token budget (SQLite-backed, process-safe).

兼容旧接口（add_session_tokens / get_session_tokens / reset_session /
check_budget / record_usage_dict），但存储从进程内 dict 改为 SQLite：
- 进程重启后预算不丢失
- 多 worker 部署下各进程共享同一份计数（WAL + 事务）

数据文件：``data/budget.db``（表 budget: session_id PRIMARY KEY, tokens）。
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from pathlib import Path

logger = logging.getLogger("agent.budget")

_lock = threading.Lock()

from src.domain.novel.series_paths import data_root

_DATA_DIR = data_root()
_DB_PATH = _DATA_DIR / "budget.db"

# 允许测试覆盖数据库路径（如 tmp_path）
_db_path_override: str | None = None


def set_budget_db_path(path: str | Path | None) -> None:
    """Override the SQLite file location (tests use tmp_path)."""
    global _db_path_override
    _db_path_override = str(path) if path is not None else None


def _db() -> sqlite3.Connection:
    if _db_path_override:
        p = Path(_db_path_override)
    else:
        p = _DB_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=10.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS budget ("
        " session_id TEXT PRIMARY KEY,"
        " tokens INTEGER NOT NULL DEFAULT 0,"
        " updated_at REAL NOT NULL DEFAULT 0)"
    )
    return conn


def session_token_budget() -> int:
    """Max cumulative tokens per session (0 = disabled)."""
    raw = os.getenv("AGENT_SESSION_TOKEN_BUDGET", "0").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


def get_session_tokens(session_id: str) -> int:
    if not session_id:
        return 0
    try:
        with _lock:
            conn = _db()
            try:
                row = conn.execute(
                    "SELECT tokens FROM budget WHERE session_id = ?", (session_id,)
                ).fetchone()
                return int(row[0]) if row else 0
            finally:
                conn.close()
    except Exception as exc:  # noqa: BLE001 - 存储失败退化为 0，不阻塞对话
        logger.warning("budget read failed: %s", exc)
        return 0


def add_session_tokens(session_id: str, tokens: int) -> int:
    if not session_id or tokens <= 0:
        return get_session_tokens(session_id)
    import time

    try:
        with _lock:
            conn = _db()
            try:
                conn.execute(
                    "INSERT INTO budget (session_id, tokens, updated_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(session_id) DO UPDATE SET "
                    "tokens = tokens + excluded.tokens, updated_at = excluded.updated_at",
                    (session_id, int(tokens), time.time()),
                )
                conn.commit()
                row = conn.execute(
                    "SELECT tokens FROM budget WHERE session_id = ?", (session_id,)
                ).fetchone()
                return int(row[0]) if row else int(tokens)
            finally:
                conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("budget write failed: %s", exc)
        return get_session_tokens(session_id)


def reset_session(session_id: str) -> None:
    if not session_id:
        return
    try:
        with _lock:
            conn = _db()
            try:
                conn.execute("DELETE FROM budget WHERE session_id = ?", (session_id,))
                conn.commit()
            finally:
                conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("budget reset failed: %s", exc)


def check_budget(session_id: str | None) -> str | None:
    """Return error message if session already at/over budget, else None."""
    budget = session_token_budget()
    if budget <= 0 or not session_id:
        return None
    used = get_session_tokens(session_id)
    if used >= budget:
        return (
            f"Session token budget exceeded ({used}/{budget}). "
            "Start a new session or raise AGENT_SESSION_TOKEN_BUDGET."
        )
    return None


def record_usage_dict(session_id: str | None, usage: dict | None) -> int:
    """Add total_tokens from a usage payload; return new cumulative."""
    if not session_id or not usage:
        return get_session_tokens(session_id or "")
    total = int(usage.get("total_tokens") or 0)
    if total <= 0:
        total = int(usage.get("prompt_tokens") or 0) + int(
            usage.get("completion_tokens") or 0
        )
    return add_session_tokens(session_id, total)


def clear_all() -> None:
    """Delete all budget rows (ops/debug)."""
    try:
        with _lock, _db() as conn:
            conn.execute("DELETE FROM budget")
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("budget clear failed: %s", exc)
