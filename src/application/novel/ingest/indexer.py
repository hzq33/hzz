"""Ingest Phase 4 — vector indexing + catalog sidecar + result assembly.

Extracted from the former monolithic ``ingest.py``; logic unchanged.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from src.application.novel.ingest.convert import ProgressCallback
from src.application.novel.ingest.blocks import Phase3Context

logger = logging.getLogger("agent")


async def index_and_persist(
    store,
    *,
    ctx: Phase3Context,
    doc_id: str,
    series_id: str,
    volume_no: int | None,
    document,
    mime_type: str,
    raw_md: str,
    display_title: str | None,
    on_progress: ProgressCallback | None = None,
) -> None:
    """Phase 4: index all blocks into the vector store + write catalog sidecar.

    Idempotent re-ingest: drops previous blocks for this doc_id first.
    """
    def _progress(stage: str, message: str, pct: int) -> None:
        if on_progress:
            try:
                on_progress(stage, message, pct)
            except Exception:
                pass

    _progress("embed", f"嵌入与索引（{len(ctx.all_blocks)} 块）", 75)
    if store is None:
        from src.application.novel.factory import create_novel_store

        store = create_novel_store()

    # Idempotent re-ingest: drop previous blocks for this doc_id first
    try:
        deleted = await store.delete_by_doc_id(doc_id)
        if deleted:
            logger.info("Re-ingest: deleted %d old blocks for doc_id=%s", deleted, doc_id)
    except Exception as e:
        logger.warning("delete_by_doc_id failed for %s: %s", doc_id, e)

    # Free GPU cache before embedding (attribution / other CUDA work may have fragmented VRAM)
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass

    await store.index_batch(ctx.all_blocks)
    _progress("embed", "索引写入完成", 90)

    # 后台重建向量索引：吸收本次新增的 unindexed 行（IVF_PQ 不自动包含新块）
    # 幂等；重建约数十秒，不阻塞导入主流程。
    try:
        lance = getattr(getattr(store, "_vectors", None), "_lance", None)
        if lance is not None and hasattr(lance, "ensure_vector_indices"):
            import threading

            def _rebuild_vector_index() -> None:
                try:
                    res = lance.ensure_vector_indices()
                    logger.info("Vector index rebuilt after ingest: %s", res)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Vector index rebuild after ingest failed: %s", exc)

            threading.Thread(target=_rebuild_vector_index, daemon=True).start()
    except Exception:  # noqa: BLE001
        pass

    # Catalog sidecar (ordered chapters for story analysis / UI)
    try:
        from src.domain.novel.catalog import (
            VolumeEntry,
            chapters_from_document,
            content_fingerprint,
            upsert_volume_entry,
        )
        entry = VolumeEntry(
            doc_id=doc_id,
            series_id=series_id,
            volume_no=volume_no,
            volume_title=f"第{volume_no}卷" if volume_no else "",
            title=document.title or doc_id,
            source_format=mime_type or "",
            indexed_at=datetime.now(UTC).isoformat(),
            content_fingerprint=content_fingerprint(raw_md),
            block_counts={
                "narrative": len(ctx.narrative_blocks),
                "dialogue": len(ctx.dialogue_blocks),
                "qa": len(ctx.qa_blocks),
                "character": len(ctx.character_blocks),
                "total": len(ctx.all_blocks),
            },
            chapters=chapters_from_document(document),
            needs_reindex=False,
            reindex_reason="",
        )
        from src.domain.novel.catalog import (
            ensure_series_title,
            load_catalog,
            save_catalog,
        )
        upsert_volume_entry(entry)
        cat = load_catalog(series_id)
        if cat:
            if display_title:
                cat.series_title = display_title
                save_catalog(cat)
            else:
                ensure_series_title(cat)
    except Exception as e:
        # 不静默：catalog 缺失会导致卷在前端不可见（孤儿卷），必须显式告警。
        logger.error(
            "Catalog write FAILED for doc_id=%s series_id=%s — 该卷将不可见（孤儿卷），"
            "请检查 data/catalogs 写入权限或磁盘空间: %s",
            doc_id, series_id, e,
        )

    _progress("roster", "角色名录已生成", 95)
