"""ImpersonationAgent — standalone agent for character role-play with tool access.

Supports optional tool registry: the character can call web_search, execute_code,
etc. when the user asks questions beyond their novel knowledge. Uses a two-phase
LLM call: low-temperature for tool decision, high-temperature for character reply.

Retrieval hits are retained as structured ``Citation`` objects and emitted to
the client (SSE ``citations`` event) so every reply can be traced to source text.

Split into mixins (logic unchanged):
    _imp_chat.py       chat loop / tool loop / message building (ImpersonationChatMixin)
    _imp_retrieval.py  style/fact/relation/narrative retrieval (ImpersonationRetrievalMixin)
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from typing import Any

from src.core.impersonation.chat import ImpersonationChatMixin
from src.core.impersonation.models import Citation, _hit_similarity, _hit_to_citation
from src.core.impersonation.retrieval import ImpersonationRetrievalMixin
from src.core.memory import ConversationMemory
from src.domain.character_card import CharacterCard
from src.shared.llm import SharedLLMClient
from src.tools.base import BaseTool, ToolResult
from src.tools.registry import ToolRegistry

logger = logging.getLogger("agent")


# ── System prompt suffix for tool-aware characters ─────────

class ImpersonationAgent(ImpersonationChatMixin, ImpersonationRetrievalMixin):
    """Dedicated character role-play agent with optional tool access.

    Supports a ToolRegistry for web_search, execute_code, etc.
    Uses two-phase LLM calling: low-temp for tool decision, high-temp for reply.

    Multi-turn: conversation history is preserved, the character remembers.

    Mixins provide chat/retrieval methods; this class owns construction,
    persona/card state, memory, and the public citation accessors.
    """

    def __init__(
        self,
        character: str,
        store,
        llm: SharedLLMClient,
        *,
        card: CharacterCard | None = None,
        max_history_tokens: int = 4000,
        doc_id: str | None = None,
        retrieval=None,
        enable_summarization: bool = False,
        summarize_keep_turns: int = 8,
        summarize_threshold: float = 0.8,
    ):
        self.character = character
        self._store = store
        self._llm = llm
        self.doc_id = doc_id
        self.max_history_tokens = max_history_tokens
        # 上下文压缩配置（见 config.yaml → memory）
        self.enable_summarization = bool(enable_summarization)
        self.summarize_keep_turns = max(1, int(summarize_keep_turns))
        self.summarize_threshold = min(1.0, max(0.1, float(summarize_threshold)))
        # 完整检索链路（EntityResolver + QueryRewrite + LLM 路由 + 多变体多通道 + RRF + rerank）。
        # None → _retrieve_fact_context 回退旧分型轻链路。
        self._retrieval = retrieval

        self._card = card or CharacterCard.load(character)
        if self._card is None:
            raise ValueError(
                f"No character card found for '{character}'."
                f" Build one first: await CharacterCard.build('{character}', store)"
                f" or PUT /api/v1/agent/characters/{character} after creating"
                f" data/characters/{character}.json"
            )
        # P4: stale 卡片（story_analysis 更新后）轻量刷新关系视图，不重建人设。
        # 快照为本地 JSON 读取（<10ms），失败静默降级为旧视图。
        if getattr(self._card, "stale", False):
            try:
                if self._card.refresh_relations(series_id=self._card.series_id):
                    CharacterCard.save_for_series(
                        self._card.series_id,
                        self._card.name,
                        self._card,
                        character_id=self._card.character_id,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.debug("Card relation refresh failed at impersonation load: %s", exc)

        self.memory = ConversationMemory(
            max_tokens=max_history_tokens,
            truncate_enabled=not self.enable_summarization,
        )
        self.tool_registry = ToolRegistry()
        # V5 P3：时间感知 Lorebook（系列设定书条目缓存；失败为空列表）
        self._lorebook_entries: list[dict] = []
        try:
            from src.core.impersonation._lorebook import load_lorebook_entries

            self._lorebook_entries = load_lorebook_entries(
                getattr(self._card, "series_id", "") or ""
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Lorebook init failed: %s", exc)
        self._setup_system_prompt()

        self._turn_count: int = 0
        self._last_citations: list[Citation] = []
        logger.info(
            "ImpersonationAgent ready: character=%s source=%s samples=%d tools=%d doc_id=%s",
            character,
            self._card.source_work,
            len(self._card.sample_dialogues or []),
            len(self.tool_registry.list_all()),
            doc_id or "*",
        )

    def set_doc_id(self, doc_id: str | None) -> None:
        """Lock retrieval to a volume (None = all volumes in the index)."""
        self.doc_id = doc_id or None

    def get_last_citations(self) -> list[Citation]:
        return list(self._last_citations)

    def get_last_fact_citations(self) -> list[Citation]:
        return [c for c in self._last_citations if c.role == "fact"]

    def get_last_style_citations(self) -> list[Citation]:
        return [c for c in self._last_citations if c.role == "style"]


async def create_impersonation_agent(
    character: str,
    *,
    store=None,
    llm: SharedLLMClient | None = None,
    force_rebuild_card: bool = False,
    tools: list[BaseTool] | None = None,
    doc_id: str | None = None,
) -> ImpersonationAgent:
    """Factory: build an ImpersonationAgent with auto-resolved dependencies."""
    if store is None:
        from src.application.novel.factory import create_novel_store
        store = create_novel_store()

    if llm is None:
        from src.application.novel.factory import create_impersonation_service
        svc = create_impersonation_service(store=store)
        llm = svc.llm
        if llm is None:
            from src.utils.config import load_config
            config = load_config("config.yaml")
            llm = SharedLLMClient(
                primary=config.primary_llm_config(),
                fallback=config.fallback_llm_config(),
                temperature=0.85, max_tokens=1024,
            )

    # 系列权威来源：doc_id 优先（客户端显式锁定），其次 store 首卷。
    # 传给 build 避免内部 `store.doc_ids()[0]` 首卷 fallback 产生错误系列卡。
    card_series_id = ""
    try:
        from src.application.novel.query_parse import series_id_from_doc_id

        if doc_id:
            card_series_id = series_id_from_doc_id(doc_id)
        if not card_series_id:
            dids = store.doc_ids()
            if dids:
                card_series_id = series_id_from_doc_id(dids[0])
    except Exception:  # noqa: BLE001
        pass

    card = await CharacterCard.build(
        character, store,
        force_rebuild=force_rebuild_card,
        series_id=card_series_id,
    )

    # V5 P3：修正 series_id——doc_id 是权威来源，即便缓存卡 series 为空或错误，
    # 也以 doc_id 推断的系列为准并回写（幽灵卡非空但错的场景也必须修正）。
    if card_series_id and getattr(card, "series_id", "") != card_series_id:
        try:
            card.series_id = card_series_id
            CharacterCard.save_for_series(
                card_series_id, card.name, card, character_id=card.character_id
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Card series backfill save failed: %s", exc)

    # 完整链路开关：novel_rag.impersonation_full_chain（默认 true）
    retrieval = None
    cfg: dict = {}
    try:
        from src.application.novel.factory import (
            _load_raw_config,
            create_novel_retrieval,
        )

        cfg = _load_raw_config("config.yaml")
        nr = cfg.get("novel_rag", {}) or {}
        full_chain = bool(nr.get("impersonation_full_chain", True))
        if full_chain:
            retrieval = create_novel_retrieval(store=store)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Full-chain retrieval init failed (%s); impersonation will use legacy light chain",
            exc,
        )

    agent = ImpersonationAgent(
        character=character,
        store=store,
        llm=llm,
        card=card,
        doc_id=doc_id,
        retrieval=retrieval,
        max_history_tokens=int((cfg.get("memory") or {}).get("max_history_tokens", 4000)),
        enable_summarization=bool((cfg.get("memory") or {}).get("enable_summarization", False)),
        summarize_keep_turns=int((cfg.get("memory") or {}).get("summarize_keep_turns", 8)),
        summarize_threshold=float((cfg.get("memory") or {}).get("summarize_threshold", 0.8)),
    )

    if tools:
        for t in tools:
            agent.tool_registry.register(t)
    else:
        # 默认注册 world_knowledge：扮演链路代码层强制注入角色世界知识
        # （relations/character_events），不依赖 LLM 主动调工具。
        try:
            from src.tools.builtin_world_knowledge import WorldKnowledgeTool

            agent.tool_registry.register(WorldKnowledgeTool())
            logger.info("ImpersonationAgent: registered default world_knowledge tool")
        except Exception as exc:  # noqa: BLE001
            logger.debug("world_knowledge default registration failed: %s", exc)

    return agent
