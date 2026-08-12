"""Story analysis runner — map/reduce orchestration entry point.

Extracted from the former monolithic ``story_analysis.py``; logic unchanged.
"""

from __future__ import annotations

import asyncio
import logging
import time

from src.domain.novel.story_analysis.config import (
    _load_alias_map,
    _resolve_run_settings,
    load_analysis,
    select_chapters_balanced,
    _PROMPT_VERSION,
)
from src.domain.novel.story_analysis.indexer import (
    ProgressCallback,
    _emit_progress,
    _index_and_persist,
    _series_has_relation_index,
)
from src.domain.novel.story_analysis.map_reduce import (
    _chapter_fingerprint,
    _collect_chapter_text,
    _map_chapter_with_retry,
)
from src.domain.novel.story_analysis.models import StoryAnalysisSnapshot, StoryEvidence
from src.domain.novel.story_analysis.reduce import (
    _reduce_snapshot,
    merge_volume_into_snapshot,
)

logger = logging.getLogger("agent")


async def run_story_analysis(
    *,
    series_id: str,
    store,
    llm_client=None,
    doc_id: str | None = None,
    force: bool = False,
    max_chapters: int | None = None,
    map_concurrency: int | None = None,
    map_max_chars: int | None = None,
    extract_foreshadows: bool | None = None,
    on_progress: ProgressCallback | None = None,
) -> StoryAnalysisSnapshot:
    """Map/reduce relation/event index for a series (optionally one volume, merge-safe)."""
    from src.domain.novel.catalog import load_catalog

    catalog = load_catalog(series_id)
    if not catalog or not catalog.volumes:
        raise ValueError(f"No catalog for series_id={series_id}; upload/index first")

    if any(v.needs_reindex for v in catalog.volumes if not doc_id or v.doc_id == doc_id):
        logger.warning("Catalog marks needs_reindex for %s — analysis may miss chapters", series_id)

    settings = _resolve_run_settings(
        max_chapters=max_chapters,
        map_concurrency=map_concurrency,
        map_max_chars=map_max_chars,
        extract_foreshadows=extract_foreshadows,
    )
    max_chapters_n = settings["max_chapters"]
    concurrency = settings["map_concurrency"]
    max_chars = settings["map_max_chars"]
    per_type_cap = settings["per_type_cap"]
    max_tokens = settings["max_tokens"]
    summary_max_chars = settings["summary_max_chars"]
    extract_modes = dict(settings["extract"])
    map_retry = dict(settings["map_retry"])
    entity_filter = dict(settings["entity_filter"])
    balance_by_volume = bool(settings["balance_by_volume"])

    full_fp = _chapter_fingerprint(catalog, doc_id=None)
    vol_fp = _chapter_fingerprint(catalog, doc_id=doc_id) if doc_id else full_fp
    cached = load_analysis(series_id)

    if not force and cached and cached.prompt_version == _PROMPT_VERSION:
        if doc_id:
            vol_fps = dict((cached.stats or {}).get("volume_fingerprints") or {})
            has_vol_items = any(
                e.doc_id == doc_id for e in (cached.events or [])
            ) or any(r.doc_id == doc_id for r in (cached.relations or [])) or any(
                f.introduced_doc_id == doc_id for f in (cached.foreshadows or [])
            )
            if vol_fps.get(doc_id) == vol_fp and (has_vol_items or doc_id in (cached.doc_ids or [])):
                cached.stats = {**cached.stats, "cache_hit": True, "cache_scope": "volume"}
                await _emit_progress(
                    on_progress,
                    phase="cache",
                    message="命中单卷缓存",
                    chapter_done=0,
                    chapter_total=0,
                )
                if not await _series_has_relation_index(store, series_id):
                    cached = await _index_and_persist(store, cached)
                return cached
        elif cached.content_fingerprint == full_fp:
            cached.stats = {**cached.stats, "cache_hit": True, "cache_scope": "series"}
            await _emit_progress(
                on_progress,
                phase="cache",
                message="命中系列缓存",
                chapter_done=0,
                chapter_total=0,
            )
            if not await _series_has_relation_index(store, series_id):
                cached = await _index_and_persist(store, cached)
            return cached

    all_chapters = catalog.ordered_chapters(doc_id=doc_id)
    # Single-volume scope already filtered; balance only matters across volumes.
    chapters, chapters_per_doc = select_chapters_balanced(
        all_chapters,
        max_chapters=max_chapters_n,
        balance_by_volume=balance_by_volume and not doc_id,
    )
    if not chapters:
        raise ValueError("No chapters in catalog")

    total = len(chapters)
    await _emit_progress(
        on_progress,
        phase="map",
        message=f"分析中 0/{total}…",
        chapter_done=0,
        chapter_total=total,
    )

    sem = asyncio.Semaphore(concurrency)
    done_count = 0
    done_lock = asyncio.Lock()
    map_failures = 0
    t0 = time.perf_counter()
    alias_map = _load_alias_map(series_id)

    async def _one(idx: int, vol, ch) -> tuple[int, str, int, str, dict, list[StoryEvidence], bool]:
        nonlocal done_count, map_failures
        async with sem:
            text, pool = _collect_chapter_text(
                store,
                doc_id=vol.doc_id,
                chapter_title=ch.title,
                chapter_order=ch.order,
                max_chars=max_chars,
            )
            failed = False
            try:
                payload = await _map_chapter_with_retry(
                    llm_client,
                    series_id=series_id,
                    doc_id=vol.doc_id,
                    chapter_title=ch.title,
                    chapter_order=ch.order,
                    chapter_text=text,
                    evidence_pool=pool,
                    extract=extract_modes,
                    per_type_cap=per_type_cap,
                    max_tokens=max_tokens,
                    summary_max_chars=summary_max_chars,
                    map_retry=map_retry,
                )
            except Exception as e:
                logger.warning("Map chapter failed %s/%s: %s", vol.doc_id, ch.title, e)
                payload = {
                    "events": [],
                    "foreshadows": [],
                    "relations": [],
                    "_meta": {"parse_failed": True, "likely_truncated": False, "raw_chars": 0},
                }
                failed = True
            async with done_lock:
                if failed:
                    map_failures += 1
                done_count += 1
                cur = done_count
            await _emit_progress(
                on_progress,
                phase="map",
                message=f"分析中 {cur}/{total}…",
                chapter_done=cur,
                chapter_total=total,
                chapter_title=ch.title,
                doc_id=vol.doc_id,
            )
            return idx, vol.doc_id, ch.order, ch.title, payload, pool, failed

    tasks = [_one(i, vol, ch) for i, (vol, ch) in enumerate(chapters)]
    raw_results = await asyncio.gather(*tasks)
    raw_results.sort(key=lambda x: x[0])
    chapter_results = [
        (doc, order, title, payload, pool)
        for _, doc, order, title, payload, pool, _ in raw_results
    ]

    await _emit_progress(
        on_progress,
        phase="reduce",
        message="合并结果…",
        chapter_done=total,
        chapter_total=total,
    )

    partial_doc_ids = list(dict.fromkeys(vol.doc_id for vol, _ in chapters))
    reduce_fp = vol_fp if doc_id else full_fp
    snap = _reduce_snapshot(
        series_id,
        partial_doc_ids,
        reduce_fp,
        chapter_results,
        extract=extract_modes,
        summary_max_chars=summary_max_chars,
        entity_filter=entity_filter,
        alias_map=alias_map,
    )
    snap.stats["elapsed_ms"] = int((time.perf_counter() - t0) * 1000)
    snap.stats["cache_hit"] = False
    snap.stats["map_failures"] = map_failures
    snap.stats["chapters_total"] = total
    snap.stats["chapters_done"] = total
    snap.stats["map_concurrency"] = concurrency
    snap.stats["extract_modes"] = extract_modes
    snap.stats["chapters_per_doc"] = chapters_per_doc
    snap.stats["empty_index"] = (
        len(snap.events) == 0 and len(snap.relations) == 0
    )
    vol_fps = dict((snap.stats or {}).get("volume_fingerprints") or {})
    if doc_id:
        vol_fps[doc_id] = vol_fp
        for d in partial_doc_ids:
            vol_fps.setdefault(d, vol_fp if d == doc_id else vol_fps.get(d, ""))
        snap.stats["volume_fingerprints"] = vol_fps
        snap = merge_volume_into_snapshot(
            cached, snap, doc_id=doc_id, full_fingerprint=full_fp
        )
        snap.stats["elapsed_ms"] = int((time.perf_counter() - t0) * 1000)
        snap.stats["cache_hit"] = False
        snap.stats["map_failures"] = map_failures
        snap.stats["chapters_total"] = total
        snap.stats["chapters_done"] = total
        snap.stats["map_concurrency"] = concurrency
        snap.stats["extract_modes"] = extract_modes
        snap.stats["chapters_per_doc"] = chapters_per_doc
        snap.stats["empty_index"] = (
            len(snap.events) == 0 and len(snap.relations) == 0
        )
        snap.stats["volume_fingerprints"] = {
            **dict((cached.stats or {}).get("volume_fingerprints") or {} if cached else {}),
            **vol_fps,
            doc_id: vol_fp,
        }
    else:
        for d in partial_doc_ids:
            vol_fps[d] = _chapter_fingerprint(catalog, doc_id=d)
        snap.stats["volume_fingerprints"] = vol_fps
        snap.content_fingerprint = full_fp

    await _emit_progress(
        on_progress,
        phase="index",
        message="写入关系/事件索引…",
        chapter_done=total,
        chapter_total=total,
    )
    snap = await _index_and_persist(store, snap)
    await _emit_progress(
        on_progress,
        phase="done",
        message=(
            "完成（未抽出有效线索）"
            if snap.stats.get("empty_index")
            else "完成"
        ),
        chapter_done=total,
        chapter_total=total,
    )
    return snap
