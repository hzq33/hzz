"""Story analysis reduce stage — snapshot reduction and volume merge.

Extracted from the former monolithic ``story_analysis.py``; logic unchanged.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from src.domain.novel.story_analysis.config import (
    _canon_name,
    _is_weak_entity_name,
    _load_alias_map,
    _DEFAULT_REJECT_SUBSTRINGS,
    _DEFAULT_SUMMARY_MAX_CHARS,
    _PROMPT_VERSION,
)
from src.domain.novel.story_analysis.models import (
    ForeshadowRecord,
    RelationChange,
    StoryAnalysisSnapshot,
    StoryEvent,
    StoryEvidence,
)

logger = logging.getLogger("agent")


def _bind_evidence(
    block_ids: list[str],
    pool: list[StoryEvidence],
) -> list[StoryEvidence]:
    """Bind claim evidence_block_ids to pool entries. No soft-fallback."""
    by_id = {e.block_id: e for e in pool if e.block_id}
    out: list[StoryEvidence] = []
    for bid in block_ids or []:
        if bid in by_id:
            out.append(by_id[bid])
    return out


def _norm_story_time(raw: Any) -> dict:
    """归一化 LLM 输出的 story_time（容错：非 dict/字段缺失补默认）。"""
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    year = raw.get("year")
    try:
        if year is not None and str(year).strip() not in ("", "null"):
            out["year"] = int(float(str(year).strip()))
    except (TypeError, ValueError):
        pass
    for k in ("period", "label", "relative"):
        v = str(raw.get(k) or "").strip()
        if v:
            out[k] = v
    try:
        conf = float(raw.get("confidence") or 0)
        if conf > 0:
            out["confidence"] = round(min(1.0, max(0.0, conf)), 3)
    except (TypeError, ValueError):
        pass
    # V5：转生前（回忆/前世）无 year 时补负 year（-1），保证排序在正 year 之前
    if "year" not in out and str(out.get("period") or "").strip() in ("转生前",):
        out["year"] = -1
    return out


def _reduce_snapshot(
    series_id: str,
    doc_ids: list[str],
    fingerprint: str,
    chapter_results: list[tuple[str, int, str, dict, list[StoryEvidence]]],
    *,
    extract: dict[str, bool] | None = None,
    summary_max_chars: int = _DEFAULT_SUMMARY_MAX_CHARS,
    entity_filter: dict[str, Any] | None = None,
    alias_map: dict[str, str] | None = None,
) -> StoryAnalysisSnapshot:
    modes = extract or {"relations": True, "events": True, "foreshadows": False}
    filt = entity_filter or {
        "reject_substrings": list(_DEFAULT_REJECT_SUBSTRINGS),
        "min_name_len": 2,
        "max_name_len": 16,
    }
    reject = list(filt.get("reject_substrings") or _DEFAULT_REJECT_SUBSTRINGS)
    min_len = int(filt.get("min_name_len", 2))
    max_len = int(filt.get("max_name_len", 16))
    aliases = alias_map if alias_map is not None else _load_alias_map(series_id)
    sm_cap = max(20, int(summary_max_chars or _DEFAULT_SUMMARY_MAX_CHARS))

    events: list[StoryEvent] = []
    foreshadows: list[ForeshadowRecord] = []
    relations: list[RelationChange] = []
    seen_event: set[str] = set()
    dropped_no_evidence = 0
    dropped_weak_entity = 0
    parse_failures = 0
    truncated_chapters = 0
    retry_attempts = 0
    retry_successes = 0
    chapters_with_claims = 0

    for doc_id, order, title, payload, pool in chapter_results:
        meta = payload.get("_meta") if isinstance(payload.get("_meta"), dict) else {}
        if meta.get("parse_failed"):
            parse_failures += 1
        if meta.get("likely_truncated") or str(meta.get("finish_reason") or "").lower() == "length" or (
            meta.get("first_truncated")
            or str(meta.get("first_finish_reason") or "").lower() == "length"
        ):
            truncated_chapters += 1
        if meta.get("retry_attempted"):
            retry_attempts += int(meta.get("retry_attempts") or 1)
        if meta.get("retry_success"):
            retry_successes += 1

        chapter_claim_count = 0

        if modes.get("events"):
            for ev in payload.get("events") or []:
                summary = str(ev.get("summary") or "").strip()[:sm_cap]
                if not summary:
                    continue
                # V5：后记/作者注/特典说明等非剧情事件直接跳过
                if any(
                    w in summary
                    for w in ("后记", "作者注", "作者的话", "感谢读者", "网络版读者", "插画师")
                ):
                    dropped_weak_entity += 1
                    continue
                key = f"{doc_id}:{order}:{summary[:40]}"
                if key in seen_event:
                    continue
                seen_event.add(key)
                evid = _bind_evidence(list(ev.get("evidence_block_ids") or []), pool)
                if not evid:
                    dropped_no_evidence += 1
                    continue
                chars_raw = list(ev.get("characters") or [])[:8]
                chars: list[str] = []
                for c in chars_raw:
                    c2 = _canon_name(str(c or "").strip(), aliases)
                    if _is_weak_entity_name(
                        c2,
                        reject_substrings=reject,
                        min_name_len=min_len,
                        max_name_len=max_len,
                    ):
                        dropped_weak_entity += 1
                        continue
                    chars.append(c2)
                events.append(
                    StoryEvent(
                        event_id=f"ev_{uuid.uuid4().hex[:10]}",
                        summary=summary,
                        event_type=str(ev.get("event_type") or "plot"),
                        characters=chars,
                        confidence=float(ev.get("confidence") or 0.5),
                        evidence=evid,
                        doc_id=doc_id,
                        chapter_order=order,
                        chapter_title=title,
                        story_time=_norm_story_time(ev.get("story_time")),
                    )
                )
                chapter_claim_count += 1

        if modes.get("foreshadows"):
            for fh in payload.get("foreshadows") or []:
                content = str(fh.get("content") or "").strip()[:sm_cap]
                if not content:
                    continue
                evid = _bind_evidence(list(fh.get("evidence_block_ids") or []), pool)
                if not evid:
                    dropped_no_evidence += 1
                    continue
                related_raw = list(fh.get("related_characters") or [])[:8]
                related: list[str] = []
                for c in related_raw:
                    c2 = _canon_name(str(c or "").strip(), aliases)
                    if _is_weak_entity_name(
                        c2,
                        reject_substrings=reject,
                        min_name_len=min_len,
                        max_name_len=max_len,
                    ):
                        dropped_weak_entity += 1
                        continue
                    related.append(c2)
                foreshadows.append(
                    ForeshadowRecord(
                        foreshadow_id=f"fh_{uuid.uuid4().hex[:10]}",
                        content=content,
                        status=str(fh.get("status") or "pending"),
                        related_characters=related,
                        introduced_chapter=order,
                        introduced_doc_id=doc_id,
                        confidence=float(fh.get("confidence") or 0.5),
                        evidence=evid,
                    )
                )
                chapter_claim_count += 1

        if modes.get("relations"):
            for rel in payload.get("relations") or []:
                src = _canon_name(str(rel.get("source") or "").strip(), aliases)
                tgt = _canon_name(str(rel.get("target") or "").strip(), aliases)
                if not src or not tgt or src == tgt:
                    if src or tgt:
                        dropped_weak_entity += 1
                    continue
                if _is_weak_entity_name(
                    src,
                    reject_substrings=reject,
                    min_name_len=min_len,
                    max_name_len=max_len,
                ) or _is_weak_entity_name(
                    tgt,
                    reject_substrings=reject,
                    min_name_len=min_len,
                    max_name_len=max_len,
                ):
                    dropped_weak_entity += 1
                    continue
                evid = _bind_evidence(list(rel.get("evidence_block_ids") or []), pool)
                if not evid:
                    dropped_no_evidence += 1
                    continue
                relations.append(
                    RelationChange(
                        change_id=f"rc_{uuid.uuid4().hex[:10]}",
                        source=src,
                        target=tgt,
                        relation_type=str(rel.get("relation_type") or ""),
                        polarity=str(rel.get("polarity") or "neutral"),
                        summary=str(rel.get("summary") or "").strip()[:sm_cap],
                        chapter_order=order,
                        doc_id=doc_id,
                        chapter_title=title,
                        confidence=float(rel.get("confidence") or 0.5),
                        evidence=evid,
                        story_time=_norm_story_time(rel.get("story_time")),
                    )
                )
                chapter_claim_count += 1

        if chapter_claim_count > 0:
            chapters_with_claims += 1

    n_chapters = len(chapter_results)
    coverage_ratio = (chapters_with_claims / n_chapters) if n_chapters else 0.0

    def _ev_sort_key(e: StoryEvent):
        y = (e.story_time or {}).get("year")
        if isinstance(y, int):
            return (0, y, e.doc_id, e.chapter_order, e.event_id)
        # 无 year → 降级到章节序（doc_id 保证跨卷稳定序）
        return (1, e.doc_id, e.chapter_order, e.event_id)

    events.sort(key=_ev_sort_key)

    def _rel_sort_key(r: RelationChange):
        y = (r.story_time or {}).get("year")
        if isinstance(y, int):
            return (0, y, r.doc_id, r.chapter_order, r.change_id)
        return (1, r.doc_id, r.chapter_order, r.change_id)

    relations.sort(key=_rel_sort_key)
    return StoryAnalysisSnapshot(
        series_id=series_id,
        doc_ids=doc_ids,
        content_fingerprint=fingerprint,
        prompt_version=_PROMPT_VERSION,
        events=events,
        foreshadows=foreshadows,
        relations=relations,
        stats={
            "event_count": len(events),
            "foreshadow_count": len(foreshadows),
            "relation_count": len(relations),
            "chapters_analyzed": n_chapters,
            "dropped_no_evidence": dropped_no_evidence,
            "dropped_weak_entity": dropped_weak_entity,
            "parse_failures": parse_failures,
            "truncated_chapters": truncated_chapters,
            "likely_truncated": truncated_chapters,  # alias for older UI/API
            "retry_attempts": retry_attempts,
            "retry_successes": retry_successes,
            "chapters_with_claims": chapters_with_claims,
            "coverage_ratio": round(coverage_ratio, 4),
            "extract_modes": dict(modes),
        },
    )


def merge_volume_into_snapshot(
    existing: StoryAnalysisSnapshot | None,
    partial: StoryAnalysisSnapshot,
    *,
    doc_id: str,
    full_fingerprint: str,
) -> StoryAnalysisSnapshot:
    """Replace claims for ``doc_id`` with ``partial``; keep other volumes."""
    if existing is None:
        merged = partial
    else:
        events = [e for e in existing.events if e.doc_id != doc_id] + list(partial.events)
        relations = [r for r in existing.relations if r.doc_id != doc_id] + list(
            partial.relations
        )
        foreshadows = [
            f for f in existing.foreshadows if f.introduced_doc_id != doc_id
        ] + list(partial.foreshadows)
        doc_ids = list(
            dict.fromkeys(list(existing.doc_ids or []) + list(partial.doc_ids or []) + [doc_id])
        )
        vol_fps = dict((existing.stats or {}).get("volume_fingerprints") or {})
        vol_fps.update(dict((partial.stats or {}).get("volume_fingerprints") or {}))
        merged = StoryAnalysisSnapshot(
            series_id=partial.series_id or existing.series_id,
            doc_ids=doc_ids,
            content_fingerprint=full_fingerprint,
            prompt_version=_PROMPT_VERSION,
            events=events,
            foreshadows=foreshadows,
            relations=relations,
            stats={
                **dict(existing.stats or {}),
                **dict(partial.stats or {}),
                "event_count": len(events),
                "foreshadow_count": len(foreshadows),
                "relation_count": len(relations),
                "volume_fingerprints": vol_fps,
                "merged_doc_id": doc_id,
            },
        )
    merged.content_fingerprint = full_fingerprint
    merged.prompt_version = _PROMPT_VERSION

    def _ev_key(e):
        y = (e.story_time or {}).get("year")
        if isinstance(y, int):
            return (0, y, e.doc_id, e.chapter_order, e.event_id)
        return (1, e.doc_id, e.chapter_order, e.event_id)

    def _rel_key(r):
        y = (r.story_time or {}).get("year")
        if isinstance(y, int):
            return (0, y, r.doc_id, r.chapter_order, r.change_id)
        return (1, r.doc_id, r.chapter_order, r.change_id)

    merged.events = sorted(merged.events, key=_ev_key)
    merged.relations = sorted(merged.relations, key=_rel_key)
    merged.stats["event_count"] = len(merged.events)
    merged.stats["foreshadow_count"] = len(merged.foreshadows)
    merged.stats["relation_count"] = len(merged.relations)
    return merged


