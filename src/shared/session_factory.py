"""Session store factory — JSON files or SQLite (default)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

from src.shared.session_store import SessionStore
from src.shared.sqlite_session_store import DEFAULT_DB_PATH, SqliteSessionStore


class SessionStoreLike(Protocol):
    def save(self, session_id: str, messages: list, metadata: dict | None = None) -> None: ...
    def load(self, session_id: str) -> dict | None: ...
    def delete(self, session_id: str) -> bool: ...
    def list_sessions(self) -> list[str]: ...
    def cleanup_old_sessions(self, keep: int = 20) -> int: ...


def create_session_store(
    *,
    namespace: str = "chat",
    base_dir: Path | None = None,
    backend: str | None = None,
) -> SessionStore | SqliteSessionStore:
    """Create a session store.

    Env:
      AGENT_SESSION_BACKEND=sqlite|json  (default: sqlite)
      AGENT_SESSION_DB=path/to/sessions.db (sqlite only)
    """
    chosen = (backend or os.getenv("AGENT_SESSION_BACKEND", "sqlite")).strip().lower()
    if chosen in {"json", "file"}:
        return SessionStore(base_dir=base_dir)

    db_env = os.getenv("AGENT_SESSION_DB", "").strip()
    if db_env:
        db_path = Path(db_env)
    elif base_dir is not None:
        db_path = Path(base_dir) / "sessions.db"
    else:
        db_path = DEFAULT_DB_PATH
    return SqliteSessionStore(db_path=db_path, namespace=namespace)
