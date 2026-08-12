"""Hierarchical novel store — hybrid retrieval with keyword + vector + RRF.

Wraps NovelVectorStore. Combines keyword coarse recall with vector search
via Reciprocal Rank Fusion.

Architecture:
    query → KeywordsIndex (keyword recall)
          → NovelVectorStore (vector recall)
          → RRF fuse → results

Same public API as NovelVectorStore — drop-in replacement.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from src.domain.novel.models import NovelBlock
from src.infrastructure.fusion import rrf_fuse_hits
from src.infrastructure.keyword_index import KeywordsIndex, extract_query_keywords
from src.infrastructure.novel_store import NovelVectorStore, SearchResultWithBlock

logger = logging.getLogger("agent")

# 关键词命中上限：BM25 召回后每个 gid 都要还原 block，防止宽泛查询
# 命中几百块时拖垮单次检索（配合 KeywordsIndex.search(limit=...) 使用）。
_KEYWORD_HIT_CAP = 50


class HierarchicalNovelStore:
    """Hybrid retrieval store wrapping NovelVectorStore."""

    __slots__ = ("_vectors", "_keywords", "_default_top_k", "_rrf_k")

    def __init__(
        self,
        vector_store: NovelVectorStore | None = None,
        keyword_index: KeywordsIndex | None = None,
        default_top_k: int = 5,
        rrf_k: int = 60,
    ):
        self._vectors = vector_store or NovelVectorStore()
        self._keywords = keyword_index or KeywordsIndex()
        self._default_top_k = default_top_k
        self._rrf_k = rrf_k

    def ensure_keyword_index(self, *, force: bool = False) -> None:
        """Rebuild the in-memory keyword index from the vector store.

        KeywordsIndex is memory-only; after a process restart it is empty and
        the hybrid keyword path silently degrades to vector-only. This rebuilds
        it from LanceDB (or FAISS metadata) when the index is empty.

        ``force=True`` 从表全量重建（ingest 新增块后调用，保证常驻共享索引
        立即包含新块——避免"运行时上传后 keyword 路检索失效"）。
        """
        if not force and self._keywords.stats().get("total_ids", 0) > 0:
            return  # already populated
        blocks = self._vectors.iter_blocks()
        if not blocks:
            return
        self._keywords.clear()
        self._keywords.index_batch(blocks)
        logger.info(
            "Keyword index %s: %d blocks (%d bigrams)",
            "rebuilt" if force else "rebuilt from store",
            len(blocks),
            self._keywords.stats().get("bigram_entries", 0),
        )

    async def index(self, block: NovelBlock) -> None:
        self._keywords.index(block)
        await self._vectors.index(block)

    async def index_batch(self, blocks: list[NovelBlock]) -> int:
        self._keywords.index_batch(blocks)
        return await self._vectors.index_batch(blocks)

    def _keyword_hits(
        self,
        query: str,
        channel: str,
        doc_id: str | None,
        filters: dict | None,
        top_k: int,
    ) -> list[SearchResultWithBlock]:
        """Build a ranked keyword-hit list for RRF.

        Uses BM25-scored keyword recall (see KeywordsIndex.search) — the
        returned ids are already ordered by BM25 score, so rank order feeds
        RRF directly.
        """
        # 角色名来源优先用索引里实际存在的名字（list_characters() 只含
        # character 块缓存，narrative/dialogue 块的角色名不在其中）
        characters = self._keywords.char_names() or self._vectors.list_characters()
        keywords = extract_query_keywords(query, characters)
        char_filter = None
        chapter_filter = None
        if filters:
            chars = filters.get("characters") or filters.get("character")
            if isinstance(chars, str):
                char_filter = chars
            elif chars:
                char_filter = list(chars)[0]
            chapter_filter = filters.get("chapter") or filters.get("chapter_title")

        ranked_ids = self._keywords.search(
            keywords,
            block_type=channel,
            character=char_filter,
            chapter=chapter_filter,
            # 防御：限制命中数，避免每命中一次 LanceDB 往返
            limit=max(top_k, _KEYWORD_HIT_CAP),
        ) if (keywords or char_filter or chapter_filter) else []

        hits: list[SearchResultWithBlock] = []
        if ranked_ids:
            # 批量还原 block：一次 LanceDB 查询替代 N 次 get_block 往返
            blocks = self._vectors.get_blocks(ranked_ids)
            for gid in ranked_ids:
                block = blocks.get(gid)
                if block is None:
                    continue
                if doc_id and block.doc_id != doc_id:
                    continue
                # Keyword recall: no vector similarity (UI must not show fake %).
                hits.append(
                    SearchResultWithBlock(
                        block=block,
                        score=0.0,
                        channel=channel,
                        similarity=None,
                        rank_score=0.0,
                    )
                )
                if len(hits) >= top_k:
                    break
        return hits

    async def search(
        self,
        query: str,
        channel: str = "narrative",
        doc_id: str | None = None,
        top_k: int = 5,
        min_score: float | None = None,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchResultWithBlock]:
        """Hybrid search: vector + keyword recall fused with RRF."""
        t_start = time.perf_counter()
        vector_hits = await self._vectors.search(
            query,
            channel=channel,
            doc_id=doc_id,
            top_k=top_k * 3,
            min_score=min_score,
            filters=filters,
        )
        keyword_hits = self._keyword_hits(query, channel, doc_id, filters, top_k * 3)

        if not keyword_hits:
            fused = vector_hits[:top_k]
        elif not vector_hits:
            fused = keyword_hits[:top_k]
        else:
            fused = rrf_fuse_hits(
                [vector_hits, keyword_hits],
                k=self._rrf_k,
                top_k=top_k,
            )
            logger.debug(
                "Hybrid search channel=%s vector=%d keyword=%d fused=%d",
                channel, len(vector_hits), len(keyword_hits), len(fused),
            )
        # ── RAG trace：store 层检索日志（覆盖扮演等不经 NovelRetrieval 的路径）──
        try:
            from src.shared.rag_trace import append_trace, hit_preview

            append_trace(
                {
                    "kind": "store_search",
                    "query": query,
                    "channel": channel,
                    "doc_id": doc_id,
                    "filters": filters or None,
                    "hit_count": len(fused),
                    "zero_hit": not fused,
                    "hits": [
                        {
                            "global_id": h.block.global_id,
                            "block_type": h.block.block_type,
                            "doc_id": h.block.doc_id,
                            "chapter_title": h.block.chapter_title or "",
                            "score": round(float(h.score or 0), 4),
                            "preview": hit_preview(h.block, max_len=140),
                        }
                        for h in fused[:6]
                    ],
                    "elapsed_ms": int((time.perf_counter() - t_start) * 1000),
                }
            )
        except Exception:  # noqa: BLE001
            pass
        return fused

    async def search_multi(
        self,
        query: str,
        channel_weights: dict[str, float],
        doc_id: str | None = None,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
        fusion: str = "rrf",
        rrf_k: int = 60,
    ) -> list[SearchResultWithBlock]:
        """Multi-channel hybrid search with RRF across channels."""
        per_channel: list[list[SearchResultWithBlock]] = []
        for channel, weight in channel_weights.items():
            if weight <= 0:
                continue
            hits = await self.search(
                query,
                channel=channel,
                doc_id=doc_id,
                top_k=top_k * 2,
                filters=filters,
            )
            per_channel.append(hits)

        if fusion == "weighted":
            return await self._vectors.search_multi(
                query,
                channel_weights,
                doc_id=doc_id,
                top_k=top_k,
                filters=filters,
                fusion="weighted",
            )

        return rrf_fuse_hits(per_channel, k=rrf_k or self._rrf_k, top_k=top_k)

    def doc_ids(self) -> list[str]:
        return self._vectors.doc_ids()

    def block_count(self) -> int:
        return self._vectors.block_count()

    def list_characters(self, doc_id: str | None = None) -> list[str]:
        return self._vectors.list_characters(doc_id)

    def get_block(self, global_id: str) -> NovelBlock | None:
        return self._vectors.get_block(global_id)

    def iter_blocks(
        self,
        *,
        block_type: str | None = None,
        doc_id: str | None = None,
    ) -> list:
        """Delegate to vector backend (needed by on-demand character gather)."""
        if hasattr(self._vectors, "iter_blocks"):
            return self._vectors.iter_blocks(block_type=block_type, doc_id=doc_id)
        return []

    async def delete_by_doc_id(self, doc_id: str) -> int:
        deleted = await self._vectors.delete_by_doc_id(doc_id)
        if deleted <= 0:
            return 0
        # 关键词索引是内存态：删除后必须重建，否则关键词路静默失效
        # （向量路正常 → hybrid 退化为纯向量）。Lance 路径重建成本约 1s。
        self._rebuild_keyword_index()
        return deleted

    async def delete_by_global_ids(self, global_ids: list[str]) -> int:
        """Delete blocks by global_id list (delegated to vector backend).

        供 character_channel_index.delete_relation_event_blocks 使用
        （关系/事件索引 replace 语义）。删除后重建关键词索引。
        """
        ids = [g for g in (global_ids or []) if g]
        if not ids:
            return 0
        deleted = await self._vectors.delete_by_global_ids(ids)
        if deleted > 0:
            self._rebuild_keyword_index()
        return deleted

    def _rebuild_keyword_index(self) -> None:
        """Clear and rebuild the in-memory keyword index from the vector store."""
        self._keywords.clear()
        try:
            blocks = self._vectors.iter_blocks()
            if blocks:
                self._keywords.index_batch(blocks)
                logger.info(
                    "Keyword index rebuilt after delete: %d blocks (%d bigrams)",
                    len(blocks),
                    self._keywords.stats().get("bigram_entries", 0),
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Keyword index rebuild after delete failed: %s", exc)

    async def stats(self) -> dict:
        vs = await self._vectors.stats()
        ks = self._keywords.stats()
        return {**vs, "keyword_index": ks, "hybrid": True}
