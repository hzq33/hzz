"""Novel vector store — single logical index, 3 channel vectors per record.

Wraps the existing FAISSVectorStore and EmbeddingProvider to implement
the "single-index multi-vector" pattern for novel RAG:

    Metadata store
        ↑ (1 record per global_id)
    ┌───┴───┬───────┬──────┐
    │  3 FAISS indexes      │
    │ narrative/dialogue/qa │
    └───────────────────────┘

Usage:
    store = NovelVectorStore(embedding=MockEmbeddingProvider(768))
    await store.index(block)
    results = await store.search("苏瑶是谁", channel="qa")
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, ClassVar

from src.domain.novel.models import (
    BLOCK_CHARACTER,
    BLOCK_DIALOGUE,
    BLOCK_NARRATIVE,
    BLOCK_QA,
    NovelBlock,
)
from src.infrastructure.embedding import EmbeddingProvider, MockEmbeddingProvider
from src.infrastructure.vector_store import (
    MemoryVectorStore,
    VectorRecord,
    VectorStore,
)

logger = logging.getLogger("agent")


def _observe_rag_fallback(reason: str) -> None:
    """RAG 降级计数（prometheus 未初始化时 no-op）。"""
    try:
        from src.shared.metrics import observe_rag_fallback as _obs

        _obs(reason=reason)
    except Exception:
        pass


# ── Public result type ────────────────────────────────────────


def distance_to_similarity(distance: float) -> float:
    """Convert LanceDB ``_distance`` (lower is better) to a similarity score.

    Downstream fusion and ``min_score`` filters treat score as higher-is-better
    (same direction as FAISS/Memory cosine similarity).
    """
    d = float(distance)
    if d < 0:
        d = 0.0
    return 1.0 / (1.0 + d)


def _relax_character_filter(filters: dict[str, Any] | None) -> dict[str, Any] | None:
    """放宽 characters 过滤为姓氏/名字片段前缀匹配。

    将 ["月之木古都"] 放宽为 ["月之木", "古都", "月之"]（姓氏+名字片段），
    提高 LanceDB LIKE 命中率。保留 doc_id 和其他 filters 不变。

    CJK 名字通常 2-4 字，取前 2-3 字作为前缀片段。
    返回新 filters dict（不修改原 dict）；若无 characters 过滤返回 None。
    """
    if not filters:
        return None
    chars = filters.get("characters") or filters.get("character")
    if not chars:
        return None
    original_list = [chars] if isinstance(chars, str) else list(chars)
    if not original_list:
        return None

    relaxed_names: list[str] = []
    seen: set[str] = set()
    for name in original_list:
        name = str(name).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        relaxed_names.append(name)
        # 取 2-3 字前缀片段（CJK 名字姓氏通常 1-2 字）
        if len(name) >= 3:
            # 姓氏前缀（前 2 字）
            prefix2 = name[:2]
            if prefix2 not in seen:
                seen.add(prefix2)
                relaxed_names.append(prefix2)
        if len(name) >= 4:
            # 名字片段（第 3-4 字）
            prefix3 = name[2:4]
            if prefix3 not in seen:
                seen.add(prefix3)
                relaxed_names.append(prefix3)

    new_filters = dict(filters)
    new_filters["characters"] = relaxed_names
    return new_filters


def _block_matches_filters(block: NovelBlock, filters: dict[str, Any] | None) -> bool:
    """Apply optional metadata filters (characters / chapter / granularity / series)."""
    if not filters:
        return True
    chars = filters.get("characters") or filters.get("character")
    if chars:
        wanted = {chars} if isinstance(chars, str) else set(chars)
        present = set(getattr(block, "all_person", None) or []) | set(
            getattr(block, "characters", None) or []
        )
        name = getattr(block, "character_name", "") or ""
        if name:
            present.add(name)
        if present.isdisjoint(wanted):
            return False
    # series：doc_id 前缀匹配（doc_id 命名规则 {series}__vol{NN}）。
    # 兼容无卷后缀的单卷书（doc_id == series_id）——旧实现只认 ``__vol`` 前缀
    # 导致这类书的 vector 路 post-filter 恒零召回。
    series = filters.get("series") or filters.get("series_id")
    if series:
        prefix = f"{str(series)}__vol"
        doc = block.doc_id or ""
        if not (doc == str(series) or doc.startswith(prefix)):
            return False
    # doc_ids：卷级白名单（精确）
    doc_ids = filters.get("doc_ids")
    if doc_ids:
        wanted_ids = {str(x) for x in doc_ids if str(x).strip()}
        if wanted_ids and (block.doc_id or "") not in wanted_ids:
            return False
    chapter = filters.get("chapter") or filters.get("chapter_title")
    if chapter and (block.chapter_title or "") != chapter:
        return False
    contains_any = filters.get("chapter_contains_any") or filters.get("chapter_contains")
    if contains_any:
        title = block.chapter_title or ""
        keys = (
            [contains_any]
            if isinstance(contains_any, str)
            else [str(k) for k in contains_any if k]
        )
        if keys and not any(k in title for k in keys):
            return False
    # Parent/Child: vector hits should be children (or flat legacy)
    gran_filter = filters.get("granularity")
    if gran_filter:
        g = getattr(block, "granularity", "") or ""
        if gran_filter == "child" and g == "parent":
            return False
        if gran_filter == "parent" and g == "child":
            return False
    return True


def _is_narrative_parent_only(block: NovelBlock) -> bool:
    """True for Parent rows that must not compete in ANN (evidence-only)."""
    return (getattr(block, "granularity", "") or "") == "parent"


class SearchResultWithBlock:
    """A search result that bundles the NovelBlock with search metadata.

    ``similarity`` — vector similarity in (0, 1] (display / confidence).
    ``rank_score`` — RRF (or other fusion) rank signal; ordering only, not UI %.
    ``score`` — backward-compatible display score: prefers ``similarity``.
    """

    __slots__ = ("block", "score", "channel", "similarity", "rank_score")

    def __init__(
        self,
        block: NovelBlock,
        score: float,
        channel: str,
        *,
        similarity: float | None = None,
        rank_score: float | None = None,
    ):
        self.block = block
        self.channel = channel
        self.rank_score = float(rank_score) if rank_score is not None else None
        if similarity is not None:
            self.similarity = float(similarity)
        elif self.rank_score is None:
            # Plain vector / FAISS hit: ``score`` is similarity.
            self.similarity = float(score)
        else:
            # Rank-only hit (e.g. keyword) with no vector sim yet.
            self.similarity = None
        # Display / legacy consumers: never expose raw RRF as ``score`` when sim exists.
        if self.similarity is not None:
            self.score = self.similarity
        else:
            self.score = float(score)


# ── NovelVectorStore ──────────────────────────────────────────


class NovelVectorStore:
    """Single-logical-index, multi-vector store for novel blocks.

    Internally creates 4 FAISS indexes (one per channel) that share
    a single metadata store keyed by global_id.

    Channel collections:
        novel:narrative   — narrative_text vectors
        novel:dialogue    — dialogue_style_text vectors
        novel:qa          — question text vectors
        novel:character   — character profile vectors (name + personality + style)

    All 4 indexes reference the same NovelBlock via global_id.
    """

    _CHANNELS: ClassVar[list[str]] = [BLOCK_NARRATIVE, BLOCK_DIALOGUE, BLOCK_QA, BLOCK_CHARACTER]
    _PREFIX: ClassVar[str] = "novel"

    def __init__(
        self,
        embedding: EmbeddingProvider | None = None,
        vector_store: VectorStore | None = None,
        dimensions: int = 1024,
        *,
        backend: str = "faiss",
        lance_path: str = "./data/novel_lance",
    ):
        """Initialize the novel vector store.

        Args:
            embedding: EmbeddingProvider for text → vector.
            vector_store: Underlying VectorStore. Defaults to MemoryVectorStore.
            dimensions: Embedding dimensions (default 1024).
            backend: "faiss" (in-memory, unit-test default) or "lancedb" (persistent).
                Production callers should use ``create_novel_store()`` which defaults
                to lancedb via config.yaml.
            lance_path: Path for LanceDB storage (only when backend="lancedb").
        """
        self.embedding = embedding or MockEmbeddingProvider(dimensions=dimensions)

        if backend == "lancedb":
            from src.infrastructure.lance_backend import LanceDBBackend
            self._lance = LanceDBBackend(db_path=lance_path, dimensions=dimensions)
            self._vs = None  # type: ignore[assignment]
            self._metadata: dict[str, NovelBlock] = {}  # thin cache
            self._backend = "lancedb"
        else:
            self._vs = vector_store or MemoryVectorStore()
            self._metadata: dict[str, NovelBlock] = {}
            self._lance = None  # type: ignore[assignment]
            self._backend = "faiss"

    # ── LanceDB 同步调用移出事件循环 ──────────────────────────

    async def _lance_search(
        self,
        query_vec: list[float],
        channel: str,
        top_k: int,
        doc_id: str | None,
        filters: dict[str, Any] | None,
    ) -> list[dict]:
        """Run a blocking LanceDB ANN search in a worker thread.

        LanceDB's synchronous search (ANN + prefilter) can take tens of ms on
        large indexes; executing it directly on the event loop stalls every
        concurrent request during retrieval.
        """
        return await asyncio.to_thread(
            self._lance.search, query_vec, channel, top_k, doc_id, filters
        )

    # ── Indexing ──────────────────────────────────────────

    async def index(self, block: NovelBlock) -> None:
        """Index a single NovelBlock into all applicable channels."""
        if self._backend == "lancedb":
            await self._lance.index(block, self.embedding)
            return

        # FAISS path
        self._metadata[block.global_id] = block
        records: list[VectorRecord] = []

        for channel in self._CHANNELS:
            vec_text = block.get_vec_text(channel)
            if not vec_text:
                continue
            result = await self.embedding.embed_texts([vec_text])
            if not result.embeddings:
                continue
            records.append(VectorRecord(
                vector_id=block.global_id,
                embedding=result.embeddings[0],
                content=vec_text,
                metadata={"channel": channel, "doc_id": block.doc_id},
            ))
            await self._vs.upsert(self._col(channel), [records[-1]])

        if records:
            logger.debug("Indexed block %s → %d channels", block.global_id, len(records))

    async def index_batch(self, blocks: list[NovelBlock]) -> int:
        """Index multiple blocks. Returns count indexed."""
        if self._backend == "lancedb":
            return await self._lance.index_batch(blocks, self.embedding)
        if not blocks:
            return 0

        # FAISS/Memory: batch-embed per channel, then upsert.
        for block in blocks:
            self._metadata[block.global_id] = block

        for channel in self._CHANNELS:
            items: list[tuple[NovelBlock, str]] = []
            for block in blocks:
                vec_text = block.get_vec_text(channel)
                if vec_text:
                    items.append((block, vec_text))
            if not items:
                continue
            result = await self.embedding.embed_texts([t for _, t in items])
            if not result.embeddings:
                continue
            records = [
                VectorRecord(
                    vector_id=block.global_id,
                    embedding=emb,
                    content=vec_text,
                    metadata={"channel": channel, "doc_id": block.doc_id},
                )
                for (block, vec_text), emb in zip(items, result.embeddings)
            ]
            await self._vs.upsert(self._col(channel), records)

        return len(blocks)

    # ── Search ────────────────────────────────────────────

    async def search(
        self,
        query: str,
        channel: str = BLOCK_NARRATIVE,
        doc_id: str | None = None,
        top_k: int = 5,
        min_score: float | None = None,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchResultWithBlock]:
        """Search novels by query, routing to the specified channel.

        Args:
            query: Natural language query.
            channel: Which channel to search ("narrative" | "dialogue" | "qa" | "character").
            doc_id: Optional book filter (searches all books if None).
            top_k: Max results.
            min_score: Minimum relevance threshold.
            filters: Optional metadata filters (``characters``, ``chapter``).

        Returns:
            List of SearchResultWithBlock sorted by relevance.
        """
        if channel not in self._CHANNELS:
            raise ValueError(f"Unknown channel: {channel}. Use: {self._CHANNELS}")

        # LanceDB path
        if self._backend == "lancedb":
            result = await self.embedding.embed_texts([query])
            if not result.embeddings:
                return []
            fetch_k = top_k * 5 if channel == BLOCK_NARRATIVE else (top_k * 3 if filters else top_k)
            rows = await self._lance_search(
                result.embeddings[0],
                channel,
                fetch_k,
                doc_id,
                filters=filters,
            )

            # ── 三级 Fallback 策略 ──
            # 解决 metadata prefilter 过严导致零召回的问题。
            # 原策略：filters 零召回 → 去掉所有 filters 重试（跨系列污染根因）。
            # 新策略：分级放宽，优先保留 doc_id 隔离，最后才全去掉。
            fallback_level = 0  # 0=原始, 1=姓氏前缀, 2=去characters保留doc_id, 3=全去
            fallback_filters: dict[str, Any] | None = filters

            if not rows and filters:
                # Level 1: 放宽 characters 过滤为姓氏/名字片段前缀匹配，保留 doc_id
                relaxed = _relax_character_filter(filters)
                if relaxed is not None and relaxed != filters:
                    logger.warning(
                        "Lance search L1 fallback: relax character filter to surname-prefix, "
                        "keep doc_id. channel=%s doc_id=%s",
                        channel, doc_id or "",
                    )
                    _observe_rag_fallback("lance_l1_relax_character")
                    rows = await self._lance_search(
                        result.embeddings[0],
                        channel,
                        fetch_k,
                        doc_id,
                        filters=relaxed,
                    )
                    if rows:
                        fallback_level = 1
                        fallback_filters = relaxed

                # Level 2: 去掉 characters 过滤，保留 doc_id（同系列内搜索）
                if not rows and filters.get("characters") and doc_id:
                    logger.warning(
                        "Lance search L2 fallback: drop character filter, keep doc_id. "
                        "channel=%s doc_id=%s",
                        channel, doc_id,
                    )
                    _observe_rag_fallback("lance_l2_drop_character")
                    l2_filters = {k: v for k, v in filters.items() if k not in ("characters", "character")}
                    rows = await self._lance_search(
                        result.embeddings[0],
                        channel,
                        fetch_k,
                        doc_id,
                        filters=l2_filters or None,
                    )
                    if rows:
                        fallback_level = 2
                        fallback_filters = l2_filters or None

                # Level 3: 去掉 soft filters（characters/chapter），保留 scope 约束
                # （doc_id 参数 / series / doc_ids）。无任何 scope 时拒绝全库重试，
                # 根治跨作品检索污染（旧行为：全去 filters 重试 = 跨系列风险）。
                if not rows:
                    scope_filters = {
                        k: v for k, v in filters.items()
                        if k in ("series", "series_id", "doc_ids")
                    }
                    if scope_filters:
                        logger.warning(
                            "Lance search L3 fallback: drop soft filters, keep scope "
                            "(series/doc_ids). channel=%s doc_id=%s",
                            channel, doc_id or "",
                        )
                        _observe_rag_fallback("lance_l3_keep_scope")
                        rows = await self._lance_search(
                            result.embeddings[0],
                            channel,
                            fetch_k,
                            doc_id,
                            filters=scope_filters or None,
                        )
                        if rows:
                            fallback_level = 3
                            fallback_filters = scope_filters
                    elif not doc_id:
                        logger.warning(
                            "Lance search zero-recall with no doc_id/scope — refusing "
                            "unfiltered global fallback (cross-series pollution guard). "
                            "channel=%s",
                            channel,
                        )
                        _observe_rag_fallback("lance_refuse_global_fallback")

            hits = []
            for r in rows:
                block = self._lance._row_to_block(r)
                if block is None:
                    continue
                # Parent rows are evidence-only (zero/weak vectors) — never return as hits
                if channel == BLOCK_NARRATIVE and _is_narrative_parent_only(block):
                    continue
                score = distance_to_similarity(r.get("_distance", 0.0))
                if min_score is not None and score < min_score:
                    continue
                # fallback_level >= 2 时已放弃 characters 过滤，post-filter 也需跳过
                # fallback_level == 1 时用放宽后的 filters 做 post-filter
                if fallback_level < 2 and not _block_matches_filters(block, fallback_filters):
                    continue
                hits.append(
                    SearchResultWithBlock(
                        block=block,
                        score=score,
                        channel=channel,
                        similarity=score,
                    )
                )
            return hits[:top_k]

        # FAISS path
        result = await self.embedding.embed_texts([query])
        if not result.embeddings:
            return []
        query_vec = result.embeddings[0]

        # Search FAISS
        fetch_k = top_k * 5 if channel == BLOCK_NARRATIVE else (top_k * 3 if (doc_id or filters) else top_k)
        raw_results = await self._vs.search(
            self._col(channel),
            query_vec,
            top_k=fetch_k,
        )

        # Resolve metadata + filter
        hits: list[SearchResultWithBlock] = []
        for r in raw_results:
            block = self._metadata.get(r.vector_id)
            if block is None:
                logger.debug("Skip: no metadata for %s", r.vector_id)
                continue
            if channel == BLOCK_NARRATIVE and _is_narrative_parent_only(block):
                continue
            if doc_id and block.doc_id != doc_id:
                logger.debug("Skip: doc_id mismatch %s != %s", block.doc_id, doc_id)
                continue
            if min_score is not None and r.score < min_score:
                logger.debug("Skip: score %.3f < %.3f", r.score, min_score)
                continue
            if not _block_matches_filters(block, filters):
                continue
            hits.append(
                SearchResultWithBlock(
                    block=block,
                    score=r.score,
                    channel=channel,
                    similarity=float(r.score),
                )
            )

        return hits[:top_k]

    # ── Multi-channel search ──────────────────────────────

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
        """Search across multiple channels and fuse with RRF (default) or weights.

        Args:
            query: Natural language query.
            channel_weights: Dict mapping channel → weight (used for weighted mode
                and to decide which channels to query).
            doc_id: Optional book filter.
            top_k: Max results after fusion.
            filters: Optional metadata filters.
            fusion: ``rrf`` (default) or ``weighted``.
            rrf_k: RRF smoothing constant.

        Returns:
            Merged and re-ranked results.
        """
        from src.infrastructure.fusion import rrf_fuse_hits

        per_channel: list[list[SearchResultWithBlock]] = []
        for channel, weight in channel_weights.items():
            if weight <= 0:
                continue
            results = await self.search(
                query,
                channel=channel,
                doc_id=doc_id,
                top_k=top_k * 2,
                filters=filters,
            )
            per_channel.append(results)

        if fusion == "weighted":
            all_hits: dict[str, tuple[NovelBlock, float, str]] = {}
            for (channel, weight), results in zip(
                [(c, w) for c, w in channel_weights.items() if w > 0],
                per_channel,
            ):
                for r in results:
                    gid = r.block.global_id
                    weighted = r.score * weight
                    if gid in all_hits:
                        prev_block, prev_score, _ = all_hits[gid]
                        all_hits[gid] = (prev_block, prev_score + weighted, channel)
                    else:
                        all_hits[gid] = (r.block, weighted, r.channel)
            sorted_hits = sorted(all_hits.values(), key=lambda x: x[1], reverse=True)
            return [
                SearchResultWithBlock(
                    block=block,
                    score=score,
                    channel=ch,
                    similarity=float(score),
                )
                for block, score, ch in sorted_hits[:top_k]
            ]

        return rrf_fuse_hits(per_channel, k=rrf_k, top_k=top_k)

    # ── Management ────────────────────────────────────────

    async def delete_by_doc_id(self, doc_id: str) -> int:
        """Delete all blocks belonging to a book. Returns count deleted."""
        if self._backend == "lancedb":
            return await asyncio.to_thread(self._lance.delete_by_doc_id, doc_id)
        # FAISS path
        to_delete = [
            gid for gid, block in self._metadata.items()
            if block.doc_id == doc_id
        ]
        for gid in to_delete:
            del self._metadata[gid]
            for channel in self._CHANNELS:
                await self._vs.delete(self._col(channel), [gid])
        logger.info("Deleted doc_id=%s: %d blocks", doc_id, len(to_delete))
        return len(to_delete)

    async def delete_by_global_ids(self, global_ids: list[str]) -> int:
        """Delete blocks by global_id list. Returns count deleted."""
        ids = [g for g in (global_ids or []) if g]
        if not ids:
            return 0
        if self._backend == "lancedb":
            return await asyncio.to_thread(self._lance.delete_by_global_ids, ids)
        deleted = 0
        for gid in ids:
            if gid not in self._metadata:
                continue
            del self._metadata[gid]
            for channel in self._CHANNELS:
                await self._vs.delete(self._col(channel), [gid])
            deleted += 1
        return deleted

    def get_block(self, global_id: str) -> NovelBlock | None:
        """Get a single block by global_id."""
        if self._backend == "lancedb":
            return self._lance.get_block(global_id)
        return self._metadata.get(global_id)

    def get_blocks(self, global_ids: list[str]) -> dict[str, NovelBlock]:
        """Fetch multiple blocks by global_id (bulk, one backend query).

        Keyword-hit loops should use this instead of per-id :meth:`get_block`
        — on the LanceDB backend each get_block is a full vector search.
        """
        if not global_ids:
            return {}
        if self._backend == "lancedb":
            return self._lance.get_blocks(global_ids)
        wanted = set(global_ids)
        return {gid: b for gid, b in self._metadata.items() if gid in wanted}

    def iter_blocks(
        self,
        *,
        block_type: str | None = None,
        doc_id: str | None = None,
    ) -> list:
        """Iterate stored blocks (dialogue scan for on-demand character build)."""
        if self._backend == "lancedb":
            return self._lance.iter_blocks(block_type=block_type, doc_id=doc_id)
        out = []
        for block in self._metadata.values():
            if block_type and block.block_type != block_type:
                continue
            if doc_id and block.doc_id != doc_id:
                continue
            out.append(block)
        return out

    def doc_ids(self) -> list[str]:
        """List all indexed book IDs."""
        if self._backend == "lancedb":
            return self._lance.doc_ids()
        return list({b.doc_id for b in self._metadata.values()})

    def block_count(self) -> int:
        """Total indexed blocks."""
        if self._backend == "lancedb":
            return self._lance.block_count()
        return len(self._metadata)

    def list_characters(self, doc_id: str | None = None) -> list[str]:
        """List all character names, optionally filtered by doc_id."""
        if self._backend == "lancedb":
            return self._lance.list_characters(doc_id)
        # FAISS path
        names = []
        for block in self._metadata.values():
            if block.block_type != BLOCK_CHARACTER:
                continue
            if doc_id and block.doc_id != doc_id:
                continue
            if block.character_name and block.character_name not in names:
                names.append(block.character_name)
        return names

    async def stats(self) -> dict:
        """Return statistics about the store."""
        if self._backend == "lancedb":
            return await asyncio.to_thread(self._lance.stats)
        # FAISS path
        channels = {}
        for ch in self._CHANNELS:
            s = await self._vs.stats(self._col(ch))
            channels[ch] = s.get("total_vectors", "unknown")
        return {
            "total_blocks": len(self._metadata),
            "total_books": len(self.doc_ids()),
            "channels": channels,
            "doc_ids": self.doc_ids(),
        }

    # ── Internal ──────────────────────────────────────────

    @staticmethod
    def _col(channel: str) -> str:
        return f"novel:{channel}"
