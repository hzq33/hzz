"""Lifecycle management for persistent impersonation chat sessions."""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from src.core.impersonation_agent import create_impersonation_agent
from src.shared.llm import SharedLLMClient
from src.shared.llm_factory import create_shared_llm
from src.shared.session_factory import create_session_store
from src.shared.session_store import SessionStore, is_safe_session_id, sanitize_session_id
from src.utils.config import AgentConfig

logger = logging.getLogger("agent_server")

from src.domain.novel.series_paths import data_root

_DEFAULT_SESSION_DIR = data_root() / "sessions" / "imp"


def _configured_max_sessions(default: int = 20) -> int:
    try:
        return max(1, int(os.getenv("AGENT_IMP_MAX_SESSIONS", str(default))))
    except (TypeError, ValueError):
        logger.warning("Invalid AGENT_IMP_MAX_SESSIONS; using %d", default)
        return default


class ImpersonationSessionService:
    """Manage impersonation agents with LRU retention and per-session locks."""

    def __init__(
        self,
        max_sessions: int | None = None,
        session_dir: Path | None = None,
        session_store: SessionStore | None = None,
    ) -> None:
        self.max_sessions = max(1, int(max_sessions or _configured_max_sessions()))
        self._sessions: OrderedDict[str, dict] = OrderedDict()
        self._locks: dict[str, asyncio.Lock] = {}
        self._store = session_store or create_session_store(
            namespace="imp",
            base_dir=session_dir or _DEFAULT_SESSION_DIR,
        )

    @property
    def active_count(self) -> int:
        return len(self._sessions)

    @staticmethod
    def new_session_id() -> str:
        """Generate an ASCII-only identifier accepted by SessionStore."""
        return f"imp_{uuid.uuid4().hex[:12]}"

    @classmethod
    def resolve_session_id(cls, session_id: str | None) -> str:
        """Accept client id if safe; otherwise mint a new ASCII id.

        Legacy clients stored Chinese ids like ``利姆露_c8bfb6d2`` in localStorage;
        those must not crash chat — start a fresh session instead.
        """
        value = (session_id or "").strip()
        if is_safe_session_id(value):
            return value
        if value:
            logger.warning(
                "Ignoring invalid impersonation session_id=%r; minting new id",
                value,
            )
        return cls.new_session_id()

    def session_lock(self, session_id: str) -> asyncio.Lock:
        safe_id = sanitize_session_id(session_id)
        lock = self._locks.get(safe_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[safe_id] = lock
        return lock

    @asynccontextmanager
    async def locked_session(
        self,
        character: str,
        session_id: str | None,
        *,
        doc_id: str | None,
        store,
        config: AgentConfig,
        llm_factory: Callable[..., SharedLLMClient] = create_shared_llm,
    ) -> AsyncIterator[dict]:
        """Get/create a session and serialize work for that session."""
        resolved_id = self.resolve_session_id(session_id)
        lock = self.session_lock(resolved_id)
        async with lock:
            session = await self.get_or_create(
                character,
                resolved_id,
                doc_id=doc_id,
                store=store,
                config=config,
                llm_factory=llm_factory,
            )
            yield session

    async def get_or_create(
        self,
        character: str,
        session_id: str | None,
        *,
        doc_id: str | None,
        store,
        config: AgentConfig,
        llm_factory: Callable[..., SharedLLMClient] = create_shared_llm,
    ) -> dict:
        """Return a compatible cached session or create and restore one."""
        resolved_id = self.resolve_session_id(session_id)
        existing = self._sessions.get(resolved_id)
        if existing is not None and existing["character"] == character:
            agent = existing["agent"]
            if hasattr(agent, "set_doc_id"):
                agent.set_doc_id(doc_id)
            existing["doc_id"] = doc_id
            # doc_id 是系列权威来源：会话复用但 doc_id 变化时，角色卡系列必须对齐，
            # 否则世界知识（world_knowledge）会查错系列（幽灵卡漏洞的会话层入口）。
            if doc_id and hasattr(agent, "_card"):
                try:
                    from src.application.novel.query_parse import series_id_from_doc_id

                    want = series_id_from_doc_id(doc_id)
                    got = str(getattr(agent._card, "series_id", "") or "")
                    if want and got != want:
                        from src.domain.character_card import CharacterCard

                        agent._card.series_id = want
                        CharacterCard.save_for_series(
                            want, agent._card.name, agent._card,
                            character_id=getattr(agent._card, "character_id", ""),
                        )
                        logger.info(
                            "Session %s: card series corrected %r -> %r (doc_id lock)",
                            resolved_id, got, want,
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Session card series align failed: %s", exc)
            self._sessions.move_to_end(resolved_id)
            return existing

        if existing is not None:
            self.persist(resolved_id)
            self._sessions.pop(resolved_id, None)
            self._store.delete(resolved_id)

        llm = llm_factory(config, temperature=0.85, max_tokens=1024, endpoint="impersonation_chat")
        if inspect.isawaitable(llm):
            llm = await llm
        agent = await create_impersonation_agent(
            character=character,
            store=store,
            llm=llm,
            doc_id=doc_id,
        )

        saved = self._store.load(resolved_id)
        metadata = (saved or {}).get("metadata") or {}
        if saved and metadata.get("character") == character:
            agent.reset()
            for message in saved.get("messages") or []:
                role = message.get("role")
                if role in ("user", "assistant"):
                    agent.memory.add_message(role, message.get("content") or "")
            # 恢复上下文压缩摘要（防遗忘/防跨轮矛盾）
            try:
                summary = metadata.get("memory_summary") or ""
                if summary:
                    agent.memory.set_summary(summary)
                agent.memory.add_summarized_turns(
                    int(metadata.get("memory_summarized_turns") or 0)
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("Restore memory summary failed: %s", exc)
            logger.info(
                "Restored impersonation session %s (%d messages)",
                resolved_id,
                len(saved.get("messages") or []),
            )

        session = {
            "agent": agent,
            "character": character,
            "session_id": resolved_id,
            "doc_id": doc_id,
            "title": metadata.get("title"),
            "title_locked": bool(metadata.get("title_locked")),
        }
        self._sessions[resolved_id] = session
        self.session_lock(resolved_id)

        while len(self._sessions) > self.max_sessions:
            old_id, old_session = self._sessions.popitem(last=False)
            self._persist_session(old_id, old_session)
            self._locks.pop(old_id, None)
            logger.info("Evicted and saved impersonation session: %s", old_id)

        logger.info(
            "Impersonation session created: %s → %s doc_id=%s",
            resolved_id,
            character,
            doc_id,
        )
        return session

    def get(self, session_id: str) -> dict | None:
        safe_id = sanitize_session_id(session_id)
        session = self._sessions.get(safe_id)
        if session is not None:
            self._sessions.move_to_end(safe_id)
        return session

    def reset(self, session_id: str) -> bool:
        session = self.get(session_id)
        if session is None:
            return False
        session["agent"].reset()
        self.persist(session_id)
        return True

    def pop(self, session_id: str) -> dict | None:
        safe_id = sanitize_session_id(session_id)
        session = self._sessions.pop(safe_id, None)
        if session is not None:
            self._persist_session(safe_id, session)
        self._locks.pop(safe_id, None)
        return session

    def invalidate_character(self, name: str) -> int:
        stale_ids = [
            session_id
            for session_id, session in self._sessions.items()
            if session.get("character") == name
        ]
        for session_id in stale_ids:
            self.pop(session_id)
        return len(stale_ids)

    def persist(self, session_id: str) -> bool:
        safe_id = sanitize_session_id(session_id)
        session = self._sessions.get(safe_id)
        if session is None:
            return False
        self._persist_session(safe_id, session)
        return True

    def list_summaries(self, *, limit: int = 50) -> list[dict]:
        """List persisted impersonation sessions (newest first)."""
        limit = max(1, min(int(limit), 200))
        summaries: list[dict] = []
        seen: set[str] = set()

        # In-memory first (freshest), then disk
        for session_id, session in reversed(list(self._sessions.items())):
            if len(summaries) >= limit:
                break
            summary = self._summary_from_live(session_id, session)
            summaries.append(summary)
            seen.add(session_id)

        for session_id in self._store.list_sessions():
            if len(summaries) >= limit:
                break
            if session_id in seen:
                continue
            saved = self._store.load(session_id)
            if not saved:
                continue
            meta = saved.get("metadata") or {}
            if not meta.get("character"):
                continue
            summaries.append(self._summary_from_saved(session_id, saved))
            seen.add(session_id)

        summaries.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
        return summaries[:limit]

    def all_session_ids(self) -> set[str]:
        """全部现存会话 id（内存 + 磁盘，无上限）——评估按会话过滤用。"""
        ids = set(self._sessions.keys())
        try:
            ids.update(self._store.list_sessions())
        except Exception:  # noqa: BLE001
            pass
        return ids

    def load_history(self, session_id: str) -> dict | None:
        """Return history payload from memory or disk."""
        safe_id = sanitize_session_id(session_id)
        live = self._sessions.get(safe_id)
        if live is not None:
            agent = live["agent"]
            messages = (
                agent.memory.get_messages()
                if hasattr(agent, "memory")
                else agent.get_history()
            )
            return {
                "session_id": safe_id,
                "character": live["character"],
                "doc_id": live.get("doc_id"),
                "title": live.get("title"),
                "messages": [
                    {"role": m.get("role"), "content": m.get("content") or ""}
                    for m in messages
                    if m.get("role") in ("user", "assistant")
                ],
                "updated_at": None,
            }

        saved = self._store.load(safe_id)
        if not saved:
            return None
        meta = saved.get("metadata") or {}
        character = meta.get("character")
        if not character:
            return None
        return {
            "session_id": safe_id,
            "character": character,
            "doc_id": meta.get("doc_id"),
            "title": meta.get("title"),
            "messages": [
                {"role": m.get("role"), "content": m.get("content") or ""}
                for m in (saved.get("messages") or [])
                if m.get("role") in ("user", "assistant")
            ],
            "updated_at": saved.get("updated_at"),
        }

    def update_title(self, session_id: str, title: str) -> bool:
        safe_id = sanitize_session_id(session_id)
        clean = (title or "").strip()[:80]
        if not clean:
            return False

        live = self._sessions.get(safe_id)
        if live is not None:
            live["title"] = clean
            live["title_locked"] = True
            self._persist_session(safe_id, live)
            return True

        saved = self._store.load(safe_id)
        if not saved:
            return False
        meta = dict(saved.get("metadata") or {})
        meta["title"] = clean
        meta["title_locked"] = True
        self._store.save(safe_id, saved.get("messages") or [], metadata=meta)
        return True

    def delete_session(self, session_id: str) -> bool:
        safe_id = sanitize_session_id(session_id)
        self._sessions.pop(safe_id, None)
        self._locks.pop(safe_id, None)
        return self._store.delete(safe_id)

    def prune_persisted_sessions(self, keep: int = 50) -> int:
        """Drop oldest cold sessions on disk; keep the most recent ``keep``."""
        return int(self._store.cleanup_old_sessions(keep=max(1, int(keep))))


    def _summary_from_live(self, session_id: str, session: dict) -> dict:
        agent = session["agent"]
        messages = (
            agent.memory.get_messages()
            if hasattr(agent, "memory")
            else agent.get_history()
        )
        user_assistant = [
            m for m in messages if m.get("role") in ("user", "assistant")
        ]
        title = session.get("title") or self._auto_title(user_assistant, session["character"])
        preview = ""
        for message in reversed(user_assistant):
            content = (message.get("content") or "").strip()
            if content:
                preview = content[:80]
                break
        return {
            "session_id": session_id,
            "character": session["character"],
            "doc_id": session.get("doc_id"),
            "title": title,
            "message_count": len(user_assistant),
            "preview": preview,
            "updated_at": None,
            "active": True,
        }

    def _summary_from_saved(self, session_id: str, saved: dict) -> dict:
        meta = saved.get("metadata") or {}
        messages = [
            m for m in (saved.get("messages") or []) if m.get("role") in ("user", "assistant")
        ]
        character = meta.get("character") or "未知角色"
        title = meta.get("title") or self._auto_title(messages, character)
        preview = ""
        for message in reversed(messages):
            content = (message.get("content") or "").strip()
            if content:
                preview = content[:80]
                break
        return {
            "session_id": session_id,
            "character": character,
            "doc_id": meta.get("doc_id"),
            "title": title,
            "message_count": len(messages),
            "preview": preview,
            "updated_at": saved.get("updated_at"),
            "active": False,
        }

    @staticmethod
    def _auto_title(messages: list, character: str) -> str:
        for message in messages:
            if message.get("role") == "user":
                text = (message.get("content") or "").strip().replace("\n", " ")
                if text:
                    return text[:40] + ("…" if len(text) > 40 else "")
        return f"与{character}的对话"

    def _persist_session(self, session_id: str, session: dict) -> None:
        try:
            agent = session["agent"]
            messages = (
                agent.memory.get_messages()
                if hasattr(agent, "memory")
                else agent.get_history()
            )
            user_assistant = [
                m for m in messages if m.get("role") in ("user", "assistant")
            ]
            prior = self._store.load(session_id) or {}
            prior_meta = dict(prior.get("metadata") or {})
            title_locked = bool(
                session.get("title_locked") or prior_meta.get("title_locked")
            )
            if title_locked and (session.get("title") or prior_meta.get("title")):
                title = session.get("title") or prior_meta.get("title")
                session["title_locked"] = True
            else:
                title = self._auto_title(user_assistant, session["character"])
            session["title"] = title

            self._store.save(
                session_id,
                messages,
                metadata={
                    **prior_meta,
                    "character": session["character"],
                    "doc_id": session.get("doc_id"),
                    "title": title,
                    "title_locked": title_locked,
                    "memory_summary": agent.memory.get_summary()
                    if hasattr(agent, "memory")
                    else "",
                    "memory_summarized_turns": agent.memory.get_summarized_turns()
                    if hasattr(agent, "memory")
                    else 0,
                },
            )
        except Exception as exc:
            logger.warning("Impersonation session persist failed: %s", exc)
