"""Session Store — atomic, file-based session persistence.

Each session is saved as a JSON file under a configurable directory.
Uses atomic write-then-rename to prevent corruption on crash.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

logger = logging.getLogger("agent_server")

DEFAULT_SESSION_DIR = Path(__file__).parent.parent / "data" / "sessions"
_SAFE_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def is_safe_session_id(session_id: str | None) -> bool:
    value = (session_id or "").strip()
    return bool(value) and bool(_SAFE_SESSION_ID.match(value)) and value not in {".", ".."}


def sanitize_session_id(session_id: str) -> str:
    """Reject path traversal / unsafe session ids before joining to disk paths."""
    value = (session_id or "").strip()
    if not is_safe_session_id(value):
        raise ValueError(f"invalid session_id: {session_id!r}")
    return value


class SessionStore:
    """File-based session storage with atomic writes.

    Each session is stored as {session_id}.json containing:
        - session_id
        - messages: list of {"role": str, "content": str}
        - created_at: ISO timestamp
        - updated_at: ISO timestamp
    """

    def __init__(self, base_dir: Path | None = None):
        self._dir = Path(base_dir or DEFAULT_SESSION_DIR)
        self._dir.mkdir(parents=True, exist_ok=True)

    def session_path(self, session_id: str) -> Path:
        safe = sanitize_session_id(session_id)
        path = (self._dir / f"{safe}.json").resolve()
        if not str(path).startswith(str(self._dir.resolve())):
            raise ValueError(f"invalid session_id: {session_id!r}")
        return path

    def save(self, session_id: str, messages: list, metadata: dict | None = None) -> None:
        """Atomically save a session to disk.

        Args:
            session_id: Unique session identifier.
            messages: List of message dicts (role + content).
            metadata: Optional dict of extra session metadata (e.g. rag_character).
        """
        import datetime

        data = {
            "session_id": session_id,
            "messages": [
                {"role": m.get("role", "unknown"), "content": m.get("content", "")}
                for m in messages
                if m.get("role") in ("user", "assistant", "system")
            ],
            "updated_at": datetime.datetime.now().isoformat(),
        }
        if metadata:
            data["metadata"] = metadata

        tmp_path = self.session_path(session_id).with_suffix(".tmp")
        final_path = self.session_path(session_id)

        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, final_path)  # Atomic on same filesystem
        except Exception as e:
            logger.warning("Failed to save session %s: %s", session_id, e)
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)

    def load(self, session_id: str) -> dict | None:
        """Load a session from disk.

        Args:
            session_id: Session ID.

        Returns:
            Full session data dict with keys 'messages' and optional 'metadata',
            or None if not found.
        """
        path = self.session_path(session_id)
        if not path.exists():
            return None

        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return data
        except Exception as e:
            logger.warning("Failed to load session %s: %s", session_id, e)
            return None

    def delete(self, session_id: str) -> bool:
        """Delete a session from disk.

        Returns True if deleted, False if not found.
        """
        path = self.session_path(session_id)
        if path.exists():
            path.unlink()
            return True
        return False

    def list_sessions(self) -> list[str]:
        """List all persisted session IDs, newest first (skips unsafe filenames)."""
        files = sorted(
            self._dir.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        out: list[str] = []
        for f in files:
            if is_safe_session_id(f.stem):
                out.append(f.stem)
            else:
                logger.warning("Skipping unsafe session file: %s", f.name)
        return out

    def cleanup_old_sessions(self, keep: int = 20) -> int:
        """Remove old session files, keeping the most recent N.

        Args:
            keep: Number of most recent sessions to keep.

        Returns:
            Number of sessions pruned.
        """
        sessions = self.list_sessions()
        pruned = 0
        for sid in sessions[keep:]:
            if self.delete(sid):
                pruned += 1
        return pruned
