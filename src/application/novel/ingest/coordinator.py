"""Ingest coordinator — orchestrates the full structured pipeline.

Pipeline (unchanged from the former monolithic ``ingest.py``):
  Phase 0/1/1b  convert:  validation + format conversion + preprocessing
  Phase 2       structure: chapter parsing (regex → LLM → per-chapter repair)
  Phase 3       blocks: narrative/dialogue/qa/character blocks + roster + graph
  Phase 4       indexer: vector indexing + catalog sidecar

Extraction is behavior-preserving: error semantics and progress callbacks
match the original single-function implementation.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # avoid cycle: ingest/__init__ imports coordinator
    from src.application.novel.ingest import IngestResult

from src.application.novel.ingest.blocks import (
    IngestAbortError,
    Phase3Context,
    build_character_blocks,
    build_dialogue_blocks,
    build_graph,
    build_inventory,
    build_narrative_blocks,
    build_qa_blocks,
    narrative_known_characters,
)
from src.application.novel.ingest.convert import (
    _convert_to_md,
    _preprocess_raw_md,
    _validate_upload,
    ProgressCallback,
)
from src.domain.novel.catalog import content_fingerprint
from src.application.novel.ingest.indexer import index_and_persist
from src.application.novel.ingest.structure import _parse_structure

logger = logging.getLogger("agent")


async def ingest_novel(
    file_bytes: bytes,
    filename: str,
    *,
    store=None,
    doc_id: str | None = None,
    series_id: str | None = None,
    series_title: str | None = None,
    volume_no: int | None = None,
    generate_qa: bool = False,
    generate_character_llm: bool = False,
    on_progress: ProgressCallback | None = None,
    force_reindex: bool = False,
) -> "IngestResult":
    """Ingest a novel file through the full structured pipeline.

    Pipeline:
      1. Format conversion (epub/txt/md → raw_md)
      2. Structure parsing (raw_md → NovelDocument with chapters)
      3. Content structuring (NovelDocument → narrative/dialogue/[qa]/[character] blocks)
      4. Vector indexing + L1 CharacterRoster (no cloud LLM by default)

    Args:
        file_bytes: Raw file content.
        filename: Original filename (used for MIME detection + doc_id).
        store: NovelVectorStore. Auto-created if None.
        doc_id: Document ID. Defaults to ``{series}__volNN`` when volume detected.
        series_id: Series ID for roster/cards. Defaults from series_title or filename stem.
        series_title: Human-readable series display name (optional).
        volume_no: Optional volume number (1-based). Inferred from filename when omitted.
        generate_qa: Whether to generate QA pairs (requires LLM). Default False.
        generate_character_llm: Batch unified character LLM on ingest. Default False
            (prefer on-demand single-character build).

    Returns:
        IngestResult with indexing stats and discovered characters.
    """
    from src.application.novel.ingest import IngestResult  # local: avoid cycle

    def _progress(stage: str, message: str, pct: int) -> None:
        if on_progress:
            try:
                on_progress(stage, message, pct)
            except Exception:
                pass

    _progress("received", "已接收文件", 5)
    stem = Path(filename).stem
    display_title = (series_title or "").strip() or None
    if volume_no is None:
        from src.application.novel.ingest import infer_volume_no

        volume_no = infer_volume_no(stem)
    if series_id is None:
        # Prefer user-provided title for new series; else filename stem
        from src.application.novel.ingest import clean_series_id

        series_id = clean_series_id(display_title or stem)
    if doc_id is None:
        from src.application.novel.ingest import make_doc_id

        doc_id = make_doc_id(series_id, volume_no)

    # Build shared LLM client for character extraction (only if batch path enabled)
    persona_llm = None
    if generate_character_llm:
        try:
            from src.application.novel.ingest import _build_shared_llm

            persona_llm = _build_shared_llm(
                temperature=0.3, max_tokens=4096, endpoint="character_inventory",
            )
        except Exception as e:
            logger.warning("Failed to build persona LLM client: %s", e)

    # ── Phase 0: Input validation ─────────────────────────
    mime_type, validation_error = _validate_upload(file_bytes, filename)
    if validation_error:
        return IngestResult(
            doc_id=doc_id,
            source_format=mime_type or "unknown",
            error=validation_error,
        )

    logger.info("Ingesting '%s' as %s (%d bytes)", filename, mime_type, len(file_bytes))

    _progress("preprocess", "清洗与格式转换", 15)
    try:
        raw_md, epub_toc = _convert_to_md(file_bytes, filename, mime_type)
        if not raw_md or not raw_md.strip():
            return IngestResult(
                doc_id=doc_id, source_format=mime_type,
                error="Conversion produced empty content",
            )
    except Exception as e:
        logger.exception("Conversion failed for %s", filename)
        return IngestResult(
            doc_id=doc_id, source_format=mime_type,
            error=f"Conversion failed: {e}",
        )

    # ── Phase 1b: Preprocessing ───────────────────────────
    raw_md = _preprocess_raw_md(raw_md)
    if not raw_md or not raw_md.strip():
        return IngestResult(
            doc_id=doc_id, source_format=mime_type,
            error="Conversion produced empty content",
        )

    # ── Dedup: 同一本书已导入（内容指纹命中 catalog）→ 跳过 LLM 抽取与索引 ──
    try:
        from src.domain.novel.catalog import find_volume_by_fingerprint

        fp = find_volume_by_fingerprint(content_fingerprint(raw_md))
    except Exception as e:
        logger.debug("Dedup fingerprint check skipped: %s", e)
        fp = None
    if fp and not force_reindex:
        existing_series, existing_doc = fp
        logger.info(
            "Dedup hit: content of '%s' already indexed as %s (%s); skipping",
            filename, existing_doc, existing_series,
        )
        _progress("dedup", "已导入过相同内容，跳过", 100)
        return IngestResult(
            doc_id=existing_doc or doc_id,
            title=display_title or "",
            source_format=mime_type,
            series_id=existing_series or series_id,
            error=None,
            skipped=True,
        )
    elif fp and force_reindex:
        logger.info(
            "force_reindex: 内容已存在（%s），强制重跑全管线（%s）",
            existing_doc if (existing_doc := fp[1]) else fp[0], filename,
        )

    # ── Phase 2: Structure parsing (regex → LLM fallback → per-chapter repair) ─
    _progress("chapter", "章节检测与解析", 30)
    try:
        document = await _parse_structure(raw_md, doc_id, mime_type, epub_toc)
    except Exception as e:
        logger.exception("Structure parsing failed for %s", filename)
        return IngestResult(
            doc_id=doc_id, source_format=mime_type,
            error=f"Structure parsing failed: {e}",
        )

    # ── Phase 3: Content structuring ──────────────────────
    _progress("chunk", "切块与对话抽取", 50)
    ctx = Phase3Context()
    # Inventory first: character names must exist BEFORE narrative chunking so
    # narrative blocks get all_person tagged at chunk time (not backfilled).
    ctx.inventory_result, ctx.inventory_candidates = await build_inventory(
        document, series_id, doc_id
    )
    known_chars = narrative_known_characters(ctx.inventory_result, series_id)
    try:
        ctx.narrative_blocks, ctx.narrative_parents, ctx.cleaner = (
            await build_narrative_blocks(
                document, doc_id, mime_type, known_characters=known_chars
            )
        )
    except IngestAbortError as e:
        return IngestResult(
            doc_id=doc_id, title=document.title, source_format=mime_type, error=str(e),
        )

    ctx.dialogue_blocks = await build_dialogue_blocks(
        document,
        doc_id,
        ctx.narrative_parents,
        ctx.inventory_result,
        ctx.inventory_candidates,
        series_id,
        ctx.cleaner,
    )

    ctx.qa_blocks = await build_qa_blocks(
        ctx.narrative_parents, ctx.dialogue_blocks, generate_qa
    )

    ctx.character_blocks, ctx.roster_names = await build_character_blocks(
        document,
        doc_id,
        series_id,
        ctx.inventory_result,
        ctx.dialogue_blocks,
        ctx.narrative_blocks,
        persona_llm,
        generate_character_llm,
    )

    ctx.graph = await build_graph(
        ctx.character_blocks, ctx.dialogue_blocks, ctx.narrative_blocks, doc_id
    )

    ctx.all_blocks = (
        ctx.narrative_blocks + ctx.dialogue_blocks + ctx.qa_blocks + ctx.character_blocks
    )

    # ── Phase 4: Vector indexing + catalog ────────────────
    await index_and_persist(
        store,
        ctx=ctx,
        doc_id=doc_id,
        series_id=series_id,
        volume_no=volume_no,
        document=document,
        mime_type=mime_type,
        raw_md=raw_md,
        display_title=display_title,
        on_progress=on_progress,
    )

    # ── Build result ──────────────────────────────────────
    characters = [
        b.character_name for b in ctx.character_blocks if b.character_name
    ] or ctx.roster_names

    result = IngestResult(
        doc_id=doc_id,
        title=document.title,
        source_format=mime_type,
        series_id=series_id,
        total_chapters=document.total_chapters,
        narrative_blocks=len(ctx.narrative_blocks),
        dialogue_blocks=len(ctx.dialogue_blocks),
        qa_blocks=len(ctx.qa_blocks),
        character_blocks=len(ctx.character_blocks),
        total_blocks=len(ctx.all_blocks),
        characters=characters,
        graph=ctx.graph,
    )

    logger.info(
        "Ingested '%s': %d chapters, %d narrative + %d dialogue + %d qa + %d character = %d blocks, characters: %s",
        doc_id,
        result.total_chapters,
        result.narrative_blocks,
        result.dialogue_blocks,
        result.qa_blocks,
        result.character_blocks,
        result.total_blocks,
        characters,
    )
    _progress("done", "导入完成", 100)
    return result
