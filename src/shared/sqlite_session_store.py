"""SQLite-backed session persistence (WAL) for multi-process safety."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from src.shared.session_store import sanitize_session_id

logger = logging.getLogger("agent_server")

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "sessions" / "sessions.db"


class SqliteSessionStore:
    """Persist sessions in SQLite with WAL + busy timeout.

    Same public surface as ``SessionStore`` (save/load/delete/list/cleanup).
    """

    def __init__(
        self,
        db_path: Path | None = None,
        *,
        namespace: str = "chat",
    ) -> None:
        self.namespace = (namespace or "chat").strip() or "chat"
        self._path = Path(db_path or DEFAULT_DB_PATH)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self._path),
            timeout=30.0,
            check_same_thread=False,
            isolation_level=None,  # autocommit; we use explicit BEGIN
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _init_schema(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS sessions (
                        namespace TEXT NOT NULL,
                        session_id TEXT NOT NULL,
                        messages_json TEXT NOT NULL,
                        metadata_json TEXT,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (namespace, session_id)
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_sessions_ns_updated
                    ON sessions (namespace, updated_at DESC)
                    """
                )
            finally:
                conn.close()

    def save(
        self,
        session_id: str,
        messages: list,
        metadata: dict | None = None,
    ) -> None:
        safe_id = sanitize_session_id(session_id)
        payload_messages = [
            {"role": m.get("role", "unknown"), "content": m.get("content", "")}
            for m in messages
            if m.get("role") in ("user", "assistant", "system")
        ]
        updated_at = datetime.now().isoformat()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    """
                    INSERT INTO sessions (namespace, session_id, messages_json, metadata_json, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(namespace, session_id) DO UPDATE SET
                        messages_json=excluded.messages_json,
                        metadata_json=excluded.metadata_json,
                        updated_at=excluded.updated_at
                    """,
                    (
                        self.namespace,
                        safe_id,
                        json.dumps(payload_messages, ensure_ascii=False),
                        json.dumps(metadata or {}, ensure_ascii=False),
                        updated_at,
                    ),
                )
                conn.execute("COMMIT")
            except Exception as exc:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                logger.warning("Failed to save session %s: %s", safe_id, exc)
            finally:
                conn.close()

    def load(self, session_id: str) -> dict | None:
        safe_id = sanitize_session_id(session_id)
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    """
                    SELECT session_id, messages_json, metadata_json, updated_at
                    FROM sessions
                    WHERE namespace=? AND session_id=?
                    """,
                    (self.namespace, safe_id),
                ).fetchone()
            finally:
                conn.close()
        if row is None:
            return None
        try:
            messages = json.loads(row["messages_json"] or "[]")
            metadata = json.loads(row["metadata_json"] or "{}")
        except json.JSONDecodeError as exc:
            logger.warning("Corrupt session %s: %s", safe_id, exc)
            return None
        data = {
            "session_id": row["session_id"],
            "messages": messages,
            "updated_at": row["updated_at"],
        }
        if metadata:
            data["metadata"] = metadata
        return data

    def delete(self, session_id: str) -> bool:
        safe_id = sanitize_session_id(session_id)
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "DELETE FROM sessions WHERE namespace=? AND session_id=?",
                    (self.namespace, safe_id),
                )
                return cur.rowcount > 0
            finally:
                conn.close()

    def list_sessions(self) -> list[str]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    """
                    SELECT session_id FROM sessions
                    WHERE namespace=?
                    ORDER BY updated_at DESC
                    """,
                    (self.namespace,),
                ).fetchall()
            finally:
                conn.close()
        return [str(row["session_id"]) for row in rows]

    def cleanup_old_sessions(self, keep: int = 20) -> int:
        keep = max(0, int(keep))
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                rows = conn.execute(
                    """
                    SELECT session_id FROM sessions
                    WHERE namespace=?
                    ORDER BY updated_at DESC
                    """,
                    (self.namespace,),
                ).fetchall()
                stale = [str(r["session_id"]) for r in rows[keep:]]
                for sid in stale:
                    conn.execute(
                        "DELETE FROM sessions WHERE namespace=? AND session_id=?",
                        (self.namespace, sid),
                    )
                conn.execute("COMMIT")
                return len(stale)
            except Exception as exc:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                logger.warning("Session cleanup failed: %s", exc)
                return 0
            finally:
                conn.close()
