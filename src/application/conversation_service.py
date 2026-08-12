"""ConversationService — manages conversation session lifecycle.

Extracted from agent_server.py to provide a clean Application-layer
service for creating, retrieving, persisting, and pruning sessions.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from src.application.event_bus import EventBus
from src.core.agent import Agent
from src.core.swarm_agent import SwarmAgent
from src.domain.events import SessionCleared, SessionCreated
from src.shared.session_factory import create_session_store
from src.shared.session_store import SessionStore
from src.tools.bootstrap import register_builtin_tools

logger = logging.getLogger("agent_server")


class ConversationService:
    """Manages in-memory + persistent conversation sessions.

    Responsibilities:
    - Session creation and retrieval (LRU cache)
    - Per-session asyncio locks for concurrent request safety
    - Automatic persistence after each turn
    - History restoration from disk on restart
    - Session eviction with pre-save
    - Event emission (SessionCreated, SessionCleared)
    """

    def __init__(
        self,
        max_sessions: int = 10,
        session_dir: Path | None = None,
        event_bus: EventBus | None = None,
        session_store: SessionStore | None = None,
    ):
        self.max_sessions = max_sessions
        self._sessions: OrderedDict[str, dict] = OrderedDict()
        self._locks: dict[str, asyncio.Lock] = {}
        self._store = session_store or create_session_store(
            namespace="chat",
            base_dir=session_dir,
        )
        self._event_bus = event_bus or EventBus()

    @property
    def active_sessions(self) -> int:
        return len(self._sessions)

    @property
    def max_sessions(self) -> int:
        return self._max_sessions

    @max_sessions.setter
    def max_sessions(self, value: int):
        self._max_sessions = value

    def session_lock(self, session_id: str) -> asyncio.Lock:
        """Return (creating if needed) the lock for a session id."""
        lock = self._locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[session_id] = lock
        return lock

    @asynccontextmanager
    async def locked_session(
        self,
        session_id: str | None,
        config_factory: callable,
    ) -> AsyncIterator[dict | None]:
        """Get-or-create a session and hold its lock for the caller's turn."""
        # Resolve id first without holding a per-session lock (creation is rare).
        provisional_id = session_id
        if not provisional_id:
            # Peek: if we need a new id, allocate before locking so concurrent
            # callers with session_id=None do not share one lock key incorrectly.
            # Actual session object is still created inside get_or_create.
            provisional_id = str(uuid.uuid4())[:8]

        lock = self.session_lock(provisional_id)
        async with lock:
            session = await self.get_or_create(provisional_id, config_factory)
            yield session

    async def get_or_create(
        self,
        session_id: str | None,
        config_factory: callable,
    ) -> dict | None:
        """Get an existing session or create a new one.

        Args:
            session_id: Existing session ID (auto-generated if None).
            config_factory: Callable that returns an AgentConfig (or None).

        Returns:
            Session dict with keys: agent, streaming, session_id.
            Returns None if config is not available.
        """
        if session_id and session_id in self._sessions:
            self._sessions.move_to_end(session_id)
            return self._sessions[session_id]

        config = config_factory()
        if config is None:
            return None

        if not session_id:
            session_id = str(uuid.uuid4())[:8]

        agent = Agent(config)
        register_builtin_tools(agent.tool_registry, config)

        # Restore history from disk
        saved = self._store.load(session_id)
        if saved:
            messages = saved.get("messages", [])
            agent.memory.clear()
            for msg in messages:
                if msg.get("role") == "system":
                    continue
                agent.memory.add_message(msg["role"], msg["content"])
            logger.info("Restored session %s (%d messages)", session_id, len(messages))
        else:
            agent._set_default_system_message()

        streaming = SwarmAgent(agent, session_id=session_id, store=None)

        # Restore roleplay state from saved metadata
        if saved and saved.get("metadata"):
            meta = saved["metadata"]
            rag_char = meta.get("rag_character")
            if rag_char:
                try:
                    from src.core.swarm_agent import _enter_impersonation
                    if await _enter_impersonation(streaming, rag_char):
                        # 恢复扮演 memory 与压缩摘要（角色记忆不丢）
                        imp = streaming._imp_agent
                        if imp is not None and hasattr(imp, "memory"):
                            for msg in meta.get("rag_messages") or []:
                                role = msg.get("role")
                                if role in ("user", "assistant"):
                                    imp.memory.add_message(role, msg.get("content") or "")
                            if meta.get("rag_summary"):
                                imp.memory.set_summary(meta["rag_summary"])
                            imp.memory.add_summarized_turns(
                                int(meta.get("rag_summarized_turns") or 0)
                            )
                        logger.info("Restored impersonation mode: character=%s", rag_char)
                except Exception as e:
                    logger.warning("Failed to restore impersonation mode: %s", e)

        # 恢复后统一刷新 system prompt（工具清单可能已变化；避免过期描述）
        try:
            agent.refresh_system_prompt_for_tools()
        except Exception as exc:  # noqa: BLE001
            logger.debug("System prompt refresh failed: %s", exc)

        session = {
            "agent": agent,
            "streaming": streaming,
            "session_id": session_id,
        }
        self._sessions[session_id] = session
        self.session_lock(session_id)  # ensure lock exists

        # Evict oldest, saving first
        while len(self._sessions) > self.max_sessions:
            old_id, old_session = self._sessions.popitem(last=False)
            self._persist(old_id, old_session["agent"])
            self._locks.pop(old_id, None)
            try:
                from src.shared.session_budget import reset_session

                reset_session(old_id)
            except Exception:  # noqa: BLE001 - 预算清理失败不影响驱逐
                pass
            logger.info("Evicted and saved session: %s", old_id)

        self._event_bus.publish(SessionCreated(session_id=session_id))
        return session

    def persist(self, session_id: str, agent: Agent) -> None:
        """Save session messages to disk (call after each user turn)."""
        self._persist(session_id, agent)

    def list_active_session_ids(self) -> set[str]:
        """现存会话 id 集合：内存活跃 + 磁盘已持久化（供在线评估按会话过滤 trace）。"""
        ids = set(self._sessions.keys())
        try:
            ids.update(self._store.list_sessions())
        except Exception:  # noqa: BLE001
            pass
        return ids

    def clear(self, session_id: str) -> bool:
        """Clear a session's history and remove from disk.

        Returns True if session was found and cleared.
        """
        if session_id in self._sessions:
            agent = self._sessions[session_id]["agent"]
            agent.memory.clear()
            agent.working_memory.clear()
            agent._set_default_system_message()
            agent._turn_count = 0
            # Also reset swarm roleplay state if present
            swarm = self._sessions[session_id].get("streaming")
            if swarm is not None:
                swarm._rag_character = None
                swarm._imp_agent = None
            self._store.delete(session_id)
            self._event_bus.publish(SessionCleared(session_id=session_id))
            return True
        # Try disk cleanup even if not in memory
        self._store.delete(session_id)
        self._locks.pop(session_id, None)
        return False

    def _persist(self, session_id: str, agent: Agent) -> None:
        try:
            messages = agent.memory.get_messages()
            metadata: dict = {}
            # Save roleplay state if active
            session = self._sessions.get(session_id)
            if session:
                swarm = session.get("streaming")
                if swarm and swarm._rag_character:
                    metadata["rag_character"] = swarm._rag_character
                    # 扮演 memory 与摘要一并持久化，会话重启后恢复角色记忆
                    imp = swarm._imp_agent
                    if imp is not None and hasattr(imp, "memory"):
                        imp_messages = [
                            m for m in imp.memory.get_messages()
                            if m.get("role") in ("user", "assistant")
                        ]
                        metadata["rag_messages"] = imp_messages
                        metadata["rag_summary"] = imp.memory.get_summary()
                        metadata["rag_summarized_turns"] = imp.memory.get_summarized_turns()
            self._store.save(session_id, messages, metadata=metadata)
        except Exception as e:
            logger.warning("Session persist failed: %s", e)
