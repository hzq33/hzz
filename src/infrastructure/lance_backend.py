"""LanceDB backend for NovelVectorStore — persistent vector + metadata storage.

Replaces FAISS + in-memory dict with a single LanceDB table containing
both vector columns and metadata. Survives restarts.

Architecture:
  LanceDB table "novel_blocks"
    ├── global_id, doc_id, block_type, chapter_title  (metadata)
    ├── narrative_text, dialogues_json, question, answer  (content)
    ├── vec_narrative: list[float32;1024]
    ├── vec_dialogue:  list[float32;1024]
    ├── vec_qa:        list[float32;1024]
    └── vec_character: list[float32;1024]

Same API contract as internal FAISS+dict methods in NovelVectorStore.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import lancedb
import pyarrow as pa

from src.domain.novel.models import NovelBlock
from src.infrastructure.lance_filters import metadata_prefilter_clauses, sql_escape

logger = logging.getLogger("agent")


def _sql_escape(value: str) -> str:
    return sql_escape(value)


def _characters_for_storage(block: NovelBlock) -> list[str]:
    """Characters to persist in ``characters_json``.

    Dialogue blocks carry speakers in ``block.characters``; narrative blocks
    carry tagged names in ``block.all_person`` (empty ``characters``). Storing
    both under one column lets the character prefilter (LIKE on
    ``characters_json``) work for the narrative channel too.
    """
    chars = [str(c) for c in (block.characters or []) if c]
    for p in (block.all_person or []):
        p = str(p).strip()
        if p and p not in chars:
            chars.append(p)
    return chars


def _metadata_prefilter_clauses(filters: dict[str, Any] | None) -> list[str]:
    return metadata_prefilter_clauses(filters)

_CHANNELS = ["narrative", "dialogue", "qa", "character"]
_VEC_DIM: int = 1024


class LanceDBBackend:
    """LanceDB-based persistent backend for NovelVectorStore.

    Stores vectors + metadata in one table. Survives process restarts.
    Same internal API as the FAISS + _metadata dict approach.

    Maintains a lightweight in-memory cache (doc_ids, character_names)
    for fast enumeration queries. The ground truth is in LanceDB.
    """

    __slots__ = ("_db", "_table", "_dim", "_db_path", "_doc_ids", "_char_names")

    def __init__(self, db_path: str = "./data/novel_lance", dimensions: int = _VEC_DIM):
        self._dim = dimensions
        self._db_path = db_path
        Path(db_path).mkdir(parents=True, exist_ok=True)
        self._db = lancedb.connect(db_path)
        self._doc_ids: set[str] = set()
        self._char_names: set[str] = set()

        if "novel_blocks" in self._db.table_names():
            self._table = self._db.open_table("novel_blocks")
            self._rebuild_cache()
        else:
            self._table = self._create_table()

    # ── Vector indexes (search performance) ────────────────

    _VEC_COLUMNS = ("vec_narrative", "vec_dialogue", "vec_qa", "vec_character")

    # Text/metadata columns needed to reconstruct a NovelBlock. Scanning with a
    # column projection (excluding the 4×1024-dim vector columns) drops a full
    # iter_blocks pass from ~19s to ~1s over ~12k blocks.
    _ROW_COLUMNS = (
        "global_id", "doc_id", "block_type", "chapter_title",
        "narrative_text", "dialogues_json", "scene", "question",
        "answer", "character_name", "personality", "speech_style",
        "style_tags_json", "characters_json",
    )

    # IVF_PQ 训练最少行数（LanceDB PQ 训练下限）；不足则暴力扫描更快
    _MIN_TRAIN_ROWS = 256

    def ensure_vector_indices(self) -> dict[str, bool]:
        """Create/rebuild IVF_PQ indexes where beneficial (idempotent).

        - 列行数 < ``_MIN_TRAIN_ROWS``：跳过（暴力扫描更快，且 PQ 无法训练）
        - 索引缺失且行数足够：创建
        - 索引存在但含 unindexed 行（新导入块未进索引）：``replace=True`` 重建

        根治"新导入小说检索不到/慢"的隐患（IVF_PQ 不自动包含新块）。
        """
        created: dict[str, bool] = {}
        # 各通道块数（非零向量行数）——列投影只读 block_type，快
        counts: dict[str, int] = {}
        try:
            from collections import Counter

            arrow = self._table.to_lance().to_table(columns=["block_type"])
            counts = Counter(str(r["block_type"]) for r in arrow.to_pylist())
        except Exception as exc:  # noqa: BLE001
            logger.warning("block_type 计数失败（索引决策退化）: %s", exc)
        # 现有索引名 → unindexed 行数（IndexConfig 是对象非 dict，用 getattr）
        idx_unindexed: dict[str, int] = {}
        try:
            for info in self._table.list_indices():
                name = str(getattr(info, "name", "") or "")
                if name:
                    idx_unindexed[name] = int(getattr(info, "num_unindexed_rows", 0) or 0)
        except Exception as exc:  # noqa: BLE001
            logger.warning("list_indices failed: %s", exc)
        for col in self._VEC_COLUMNS:
            channel = col.replace("vec_", "")
            n_rows = counts.get(channel, 0)
            if n_rows < self._MIN_TRAIN_ROWS:
                # 行数不足：暴力扫描更快，不建 IVF_PQ（也不重建）
                created[col] = False
                continue
            matching = [n for n in idx_unindexed if col in n]
            unindexed = max(idx_unindexed.get(n, 0) for n in matching) if matching else 0
            if matching and unindexed <= 0:
                created[col] = False
                continue
            try:
                num_parts = max(2, min(64, n_rows // 100))
                self._table.create_index(
                    metric="cosine",
                    vector_column_name=col,
                    index_type="IVF_PQ",
                    num_partitions=num_parts,
                    num_sub_vectors=32,
                    # 缺失时新建；存在但含 unindexed 行时重建覆盖
                    replace=bool(matching),
                )
                created[col] = True
                if matching:
                    logger.info(
                        "Rebuilt IVF_PQ index on %s (absorbed %d unindexed rows)",
                        col, unindexed,
                    )
                else:
                    logger.info("Created IVF_PQ index on %s", col)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to create index on %s: %s", col, exc)
        return created

    def _create_table(self):
        """Create schema with 4 vector columns + metadata."""
        vec_type = pa.list_(pa.float32(), self._dim)

        schema = pa.schema([
            ("global_id", pa.string()),
            ("doc_id", pa.string()),
            ("block_type", pa.string()),
            ("chapter_title", pa.string()),
            ("narrative_text", pa.string()),
            ("dialogues_json", pa.string()),
            ("scene", pa.string()),
            ("question", pa.string()),
            ("answer", pa.string()),
            ("character_name", pa.string()),
            ("personality", pa.string()),
            ("speech_style", pa.string()),
            ("style_tags_json", pa.string()),
            ("characters_json", pa.string()),
            ("vec_narrative", vec_type),
            ("vec_dialogue", vec_type),
            ("vec_qa", vec_type),
            ("vec_character", vec_type),
        ])
        return self._db.create_table("novel_blocks", schema=schema, mode="overwrite")

    @staticmethod
    def _sanitize_vector(vec: list[float], dim: int) -> list[float]:
        """Replace NaN/Inf with 0.0 (LanceDB rejects NaN vectors).

        Raises ValueError when the vector dimension does not match the table
        schema — previously a mismatched embedding provider silently wrote
        all-zero vectors for every row (retrieval degrades to garbage with no
        error). Failing loudly at ingest is the correct behaviour: the schema
        dimension is fixed at table creation, so the provider or the table must
        be reconciled before re-uploading.
        """
        import math

        if len(vec) != dim:
            raise ValueError(
                f"Embedding dimension mismatch: provider returned {len(vec)}-dim "
                f"vector but LanceDB table schema is {dim}-dim. Rebuild the store "
                "(delete data/novel_lance) or switch to a matching embedding provider."
            )
        return [0.0 if math.isnan(x) or math.isinf(x) else x for x in vec]

    # ── Management ────────────────────────────────────────

    def _rebuild_cache(self) -> None:
        """Rebuild in-memory cache from LanceDB (called after restart).

        Column projection: only doc_id / character_name / block_type are needed.
        Reading the full table pulls 4×1024-dim vector columns (~19s at ~12k
        blocks); projecting to the 3 cache columns takes ~1s.

        If the projection path fails (e.g. lance API drift), fall back to a
        full read rather than leaving the cache permanently empty — an empty
        cache silently degrades doc_ids()/list_characters() consumers.
        """
        try:
            arrow = self._table.to_lance().to_table(
                columns=["doc_id", "character_name", "block_type"]
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Column-projected cache read failed (%s); falling back to full read",
                exc,
            )
            try:
                arrow = self._table.to_arrow()
            except Exception as exc2:  # noqa: BLE001
                logger.warning("Cache rebuild from LanceDB failed: %s", exc2)
                return
        rows = arrow.to_pylist()
        self._doc_ids = {r["doc_id"] for r in rows if r["doc_id"]}
        self._char_names = {
            r["character_name"] for r in rows
            if r.get("block_type") == "character" and r.get("character_name")
        }

    def _update_cache(self, block: NovelBlock) -> None:
        if block.doc_id:
            self._doc_ids.add(block.doc_id)
        # Only character blocks contribute to the global character name cache.
        # Dialogue/narrative blocks' .characters field contains per-block
        # speakers (with noise from context inference), not curated names.
        if getattr(block, "character_name", ""):
            self._char_names.add(block.character_name)

    def _remove_cache(self, doc_id: str) -> None:
        self._doc_ids.discard(doc_id)

    async def index(self, block: NovelBlock, embedding_provider) -> None:
        """Index one block: embed → write one row to LanceDB.

        Args:
            block: NovelBlock to index.
            embedding_provider: EmbeddingProvider for text → vector.
        """
        row = self._block_to_row(block)

        # Embed each channel
        for ch in _CHANNELS:
            vec_col = f"vec_{ch}"
            vec_text = block.get_vec_text(ch)
            if vec_text:
                result = await embedding_provider.embed_texts([vec_text])
                if not result.embeddings:
                    raise ValueError(
                        f"Embedding provider returned no vector for channel {ch!r} "
                        f"(global_id={block.global_id!r}); aborting ingest"
                    )
                raw = result.embeddings[0]
            else:
                raw = [0.0] * self._dim
            row[vec_col] = self._sanitize_vector(raw, self._dim)

        # Upsert by global_id (delete old + insert new)
        self._table.delete(f"global_id = '{_sql_escape(block.global_id)}'")
        self._table.add([row])
        self._update_cache(block)

    async def index_batch(self, blocks: list[NovelBlock], embedding_provider) -> int:
        """Index multiple blocks with batch embedding (one model call per channel)."""
        if not blocks:
            return 0

        # Collect all texts per channel → embed in one batch per channel
        channel_texts: dict[str, list[tuple[int, int]]] = {ch: [] for ch in _CHANNELS}
        for bi, b in enumerate(blocks):
            for ci, ch in enumerate(_CHANNELS):
                vt = b.get_vec_text(ch)
                if vt:
                    channel_texts[ch].append((bi, ci, vt))

        # Embed all channels in batch
        channel_embeddings: dict[int, dict[int, list[float]]] = {}
        for ch_name, items in channel_texts.items():
            if not items:
                continue
            texts = [t[2] for t in items]
            result = await embedding_provider.embed_texts(texts)
            if len(result.embeddings) != len(items):
                raise ValueError(
                    f"Embedding provider returned {len(result.embeddings)} vectors "
                    f"for {len(items)} texts (channel={ch_name!r}); aborting ingest "
                    "to avoid silently writing zero vectors"
                )
            for (bi, ci, _), emb in zip(items, result.embeddings):
                channel_embeddings.setdefault(bi, {})[ci] = emb

        # Build rows using batched embeddings
        rows = []
        for bi, b in enumerate(blocks):
            row = self._block_to_row(b)
            for ci, ch in enumerate(_CHANNELS):
                vec_col = f"vec_{ch}"
                emb = channel_embeddings.get(bi, {}).get(ci)
                if emb is not None:
                    raw = emb
                else:
                    raw = [0.0] * self._dim
                row[vec_col] = self._sanitize_vector(raw, self._dim)
            rows.append(row)

        # Delete existing + insert (batch add to bound failure surface on big tables)
        gids = [b.global_id for b in blocks]
        for i in range(0, len(gids), 40):
            chunk = gids[i : i + 40]
            clause = ", ".join(f"'{_sql_escape(g)}'" for g in chunk)
            self._table.delete(f"global_id IN ({clause})")
        for i in range(0, len(rows), 500):
            self._table.add(rows[i : i + 500])
        for b in blocks:
            self._update_cache(b)
        return len(blocks)

    # ── Search ────────────────────────────────────────────

    def search(
        self,
        query_vec: list[float],
        channel: str,
        top_k: int = 5,
        doc_id: str | None = None,
        filters: dict | None = None,
    ) -> list[dict]:
        """Vector search with block_type + optional doc_id / metadata prefilter.

        Each channel only searches its corresponding block_type to prevent
        zero-vector rows (non-target blocks have all-zero vectors in that
        channel's column) from polluting results with score ≈ 1.0.

        ``filters`` may include ``chapter`` / ``chapter_title`` and
        ``characters`` / ``character`` (list or str). Character prefilter uses
        a LIKE heuristic on ``characters_json``; callers should still
        post-filter for exact membership.
        """
        vec_col = f"vec_{channel}"
        builder = self._table.search(query_vec, vector_column_name=vec_col)

        # Always filter by block_type = channel — prevents zero-vector noise
        where_parts = [f"block_type = '{_sql_escape(channel)}'"]
        if doc_id:
            where_parts.append(f"doc_id = '{_sql_escape(doc_id)}'")
        where_parts.extend(_metadata_prefilter_clauses(filters))
        builder = builder.where(" AND ".join(where_parts), prefilter=True)

        return builder.limit(top_k).to_list()

    # ── Management ────────────────────────────────────────

    def delete_by_doc_id(self, doc_id: str) -> int:
        """Delete all blocks belonging to a book. Returns count."""
        before = self._table.count_rows()
        self._table.delete(f"doc_id = '{_sql_escape(doc_id)}'")
        after = self._table.count_rows()
        self._remove_cache(doc_id)
        return before - after

    def delete_by_global_ids(self, global_ids: list[str]) -> int:
        """Delete rows by global_id list. Returns count deleted."""
        ids = [g for g in (global_ids or []) if g and "'" not in g]
        if not ids:
            return 0
        before = self._table.count_rows()
        # Chunk to keep SQL short
        deleted = 0
        for i in range(0, len(ids), 40):
            chunk = ids[i : i + 40]
            clause = ", ".join(f"'{_sql_escape(g)}'" for g in chunk)
            self._table.delete(f"global_id IN ({clause})")
        after = self._table.count_rows()
        deleted = before - after
        return deleted

    def get_block(self, global_id: str) -> NovelBlock | None:
        """Get a single block by global_id.

        纯 SQL filter 按 id 直查（不经过向量索引）：无向量行（Parent 举证块、
        relation/event 索引）也能取到——此前用零向量向量搜索，全零向量行
        不在 ANN 索引可达范围，parent 等无向量块永远查不到（父子展开失效）。
        """
        gid = (global_id or "").strip()
        if not gid or "'" in gid:
            return None
        try:
            rows = (
                self._table.to_lance()
                .to_table(filter=f"global_id = '{_sql_escape(gid)}'")
                .to_pylist()
            )
            if rows:
                return self._row_to_block(rows[0])
        except Exception:  # noqa: BLE001
            pass
        return None

    def get_blocks(self, global_ids: list[str]) -> dict[str, NovelBlock]:
        """Fetch multiple blocks by global_id in bulk (one query per chunk).

        纯 SQL filter（``global_id IN (...)`）批量直查，不经过向量索引：
        - 无向量行（Parent / relation / event）可正常取到（修复前取不到）
        - 免去每次 get_block 的完整向量搜索（~0.5s/次）

        Returns:
            Mapping of global_id → NovelBlock for every id that exists in
            the table (missing ids are simply absent).
        """
        if not global_ids:
            return {}
        # Dedupe and drop ids that would break the SQL literal
        ids = list(dict.fromkeys(g for g in global_ids if g and "'" not in g))
        if not ids:
            return {}
        out: dict[str, NovelBlock] = {}
        for i in range(0, len(ids), 100):
            chunk = ids[i : i + 100]
            clause = ", ".join(f"'{_sql_escape(g)}'" for g in chunk)
            try:
                rows = (
                    self._table.to_lance()
                    .to_table(filter=f"global_id IN ({clause})")
                    .to_pylist()
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "get_blocks chunk query failed (%s); falling back per-id", exc,
                )
                for g in chunk:
                    b = self.get_block(g)
                    if b is not None:
                        out[b.global_id] = b
                continue
            for r in rows:
                b = self._row_to_block(r)
                if b is not None:
                    out[b.global_id] = b
        return out

    def iter_blocks(
        self,
        *,
        block_type: str | None = None,
        doc_id: str | None = None,
    ) -> list[NovelBlock]:
        """Scan table rows into NovelBlocks (for speaker gather / roster rebuild).

        Reads only the text/metadata columns (``_ROW_COLUMNS``) — the 4 heavy
        vector columns are not needed to rebuild NovelBlock objects, so the scan
        is ~20× cheaper on large indexes.
        """
        try:
            arrow = self._table.to_lance().to_table(columns=list(self._ROW_COLUMNS))
            rows = arrow.to_pylist()
        except Exception as e:
            logger.warning("iter_blocks failed: %s", e)
            return []
        out: list[NovelBlock] = []
        for r in rows:
            if block_type and r.get("block_type") != block_type:
                continue
            if doc_id and r.get("doc_id") != doc_id:
                continue
            block = self._row_to_block(r)
            if block is not None:
                out.append(block)
        return out

    def doc_ids(self) -> list[str]:
        """List all indexed book IDs (from cache)."""
        return sorted(self._doc_ids)

    def block_count(self) -> int:
        """Total indexed blocks."""
        return self._table.count_rows()

    def list_characters(self, doc_id: str | None = None) -> list[str]:
        """List all character names (from cache)."""
        return sorted(self._char_names)

    def stats(self) -> dict:
        """Return statistics about the store."""
        return {
            "total_blocks": self.block_count(),
            "total_books": len(self.doc_ids()),
            "doc_ids": self.doc_ids(),
            "backend": "lancedb",
            "db_path": self._db_path,
        }

    def reset(self) -> None:
        """Drop and recreate the table (for testing)."""
        self._db.drop_table("novel_blocks")
        self._table = self._create_table()
        self._doc_ids.clear()
        self._char_names.clear()

    # ── Row conversion ────────────────────────────────────

    @staticmethod
    def _block_to_row(block: NovelBlock) -> dict:
        # Persist hierarchy + relation/event meta inside style_tags_json (schema-stable).
        tags = list(block.style_tags or [])
        hier = {
            "granularity": getattr(block, "granularity", "") or "",
            "parent_id": getattr(block, "parent_id", "") or "",
            "prev_id": getattr(block, "prev_id", "") or "",
            "next_id": getattr(block, "next_id", "") or "",
        }
        relationships = getattr(block, "relationships", None) or {}
        ref_chunk_ids = list(getattr(block, "ref_chunk_ids", None) or [])
        source = getattr(block, "source", "") or ""
        background = getattr(block, "background", "") or ""
        style_payload: dict | list
        if any(hier.values()) or relationships or ref_chunk_ids or source or background:
            style_payload = {"tags": tags, "hierarchy": hier}
            if relationships:
                style_payload["relationships"] = relationships
            if ref_chunk_ids:
                style_payload["ref_chunk_ids"] = ref_chunk_ids
            if source:
                style_payload["source"] = source
            if background:
                style_payload["background"] = background
        else:
            style_payload = tags
        return {
            "global_id": block.global_id,
            "doc_id": block.doc_id,
            "block_type": block.block_type,
            "chapter_title": block.chapter_title,
            "narrative_text": block.narrative_text,
            "dialogues_json": json.dumps([{
                "turn": d.turn, "speaker": d.speaker,
                "content": d.content, "mood": d.mood,
            } for d in block.dialogues], ensure_ascii=False),
            "scene": block.scene,
            "question": block.question,
            "answer": block.answer,
            "character_name": getattr(block, "character_name", ""),
            "personality": getattr(block, "personality", ""),
            "speech_style": getattr(block, "speech_style", ""),
            "style_tags_json": json.dumps(style_payload, ensure_ascii=False),
            "characters_json": json.dumps(_characters_for_storage(block), ensure_ascii=False),
        }

    @staticmethod
    def _row_to_block(row: dict) -> NovelBlock | None:
        try:
            dialogues = []
            for d in json.loads(row.get("dialogues_json", "[]")):
                from src.domain.novel.models import DialogueTurn
                dialogues.append(DialogueTurn(
                    turn=d.get("turn", 0),
                    speaker=d.get("speaker", ""),
                    content=d.get("content", ""),
                    mood=d.get("mood", ""),
                ))
            raw_tags = json.loads(row.get("style_tags_json", "[]"))
            granularity = parent_id = prev_id = next_id = ""
            relationships: dict = {}
            ref_chunk_ids: list = []
            source = background = ""
            if isinstance(raw_tags, dict):
                tags = list(raw_tags.get("tags") or [])
                hier = raw_tags.get("hierarchy") or {}
                granularity = str(hier.get("granularity") or "")
                parent_id = str(hier.get("parent_id") or "")
                prev_id = str(hier.get("prev_id") or "")
                next_id = str(hier.get("next_id") or "")
                rel = raw_tags.get("relationships")
                if isinstance(rel, dict):
                    relationships = rel
                refs = raw_tags.get("ref_chunk_ids")
                if isinstance(refs, list):
                    ref_chunk_ids = [str(x) for x in refs if x]
                source = str(raw_tags.get("source") or "")
                background = str(raw_tags.get("background") or "")
            else:
                tags = list(raw_tags or [])
            return NovelBlock(
                global_id=row.get("global_id", ""),
                doc_id=row.get("doc_id", ""),
                source=source,
                block_type=row.get("block_type", ""),
                chapter_title=row.get("chapter_title", ""),
                narrative_text=row.get("narrative_text", ""),
                dialogues=dialogues,
                scene=row.get("scene", ""),
                question=row.get("question", ""),
                answer=row.get("answer", ""),
                character_name=row.get("character_name", ""),
                personality=row.get("personality", ""),
                speech_style=row.get("speech_style", ""),
                background=background,
                relationships=relationships,
                ref_chunk_ids=ref_chunk_ids,
                style_tags=tags,
                # 存储时 _characters_for_storage 已把 characters + all_person 合并进
                # characters_json 单列；读回后两者相同。所有消费方（post-filter、
                # QA 生成、关键词索引）均按“人名集合”使用，语义无损失；若未来需要
                # 严格区分“说话人 vs 叙事标记人名”，需加独立列并做数据迁移。
                characters=json.loads(row.get("characters_json", "[]")),
                all_person=json.loads(row.get("characters_json", "[]")),
                granularity=granularity,
                parent_id=parent_id,
                prev_id=prev_id,
                next_id=next_id,
            )
        except Exception:
            return None
