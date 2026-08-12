"""Index story-analysis relations/events into the Lance ``character`` channel.

Repurposes ``block_type=character`` from persona profiles to relation/event
lite documents (see docs/CHARACTER_CHANNEL_RELATION_EVENT_DESIGN.md).
Persona remains on CharacterCard JSON only.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Sequence
from typing import Any

from src.domain.novel.models import BLOCK_CHARACTER, NovelBlock
from src.domain.novel.story_analysis import (
    RelationChange,
    StoryAnalysisSnapshot,
    StoryEvent,
)

logger = logging.getLogger("agent")

_RELATION_SUBTYPES = frozenset({"relation", "event", "cooccur", "foreshadow"})
_MIN_CONFIDENCE = 0.35

_RELATION_QUESTION_RE = re.compile(
    r"(关系|相处|互动|对.*态度|怎么看|如何看待|后来|怎么样了|怎样了|"
    r"敌对|结盟|冲突|和好|发生了什么|关键事件|伏笔|认识吗|朋友|敌人|恋人)"
)


def _slug(text: str, *, max_len: int = 48) -> str:
    raw = re.sub(r"[^\w\u4e00-\u9fff]+", "_", (text or "").strip())
    return (raw or "x")[:max_len]


def _evidence_ids(evidence: Sequence[Any]) -> list[str]:
    out: list[str] = []
    for e in evidence or []:
        bid = getattr(e, "block_id", None) or (e.get("block_id") if isinstance(e, dict) else "")
        bid = str(bid or "").strip()
        if bid and bid not in out:
            out.append(bid)
    return out


def relation_vec_text(rel: RelationChange, *, series_id: str = "") -> str:
    parts = [
        "关系",
        rel.source,
        rel.target,
        f"类型:{rel.relation_type or '未标注'}",
        f"极性:{rel.polarity or 'neutral'}",
    ]
    if rel.summary:
        parts.append(f"摘要:{rel.summary}")
    if rel.doc_id:
        parts.append(f"卷:{rel.doc_id}")
    if rel.chapter_title:
        parts.append(f"章:{rel.chapter_title}")
    if series_id:
        parts.append(f"系列:{series_id}")
    return " ".join(parts)


def event_vec_text(ev: StoryEvent, *, series_id: str = "") -> str:
    chars = "、".join(ev.characters or [])
    parts = [
        "事件",
        ev.summary or "",
        f"类型:{ev.event_type or 'plot'}",
    ]
    if chars:
        parts.append(f"角色:{chars}")
    if ev.doc_id:
        parts.append(f"卷:{ev.doc_id}")
    if ev.chapter_title:
        parts.append(f"章:{ev.chapter_title}")
    if series_id:
        parts.append(f"系列:{series_id}")
    return " ".join(p for p in parts if p)


def relation_to_block(rel: RelationChange, *, series_id: str) -> NovelBlock | None:
    evid = _evidence_ids(rel.evidence)
    if not evid:
        return None
    if float(rel.confidence or 0) < _MIN_CONFIDENCE:
        return None
    src = (rel.source or "").strip()
    tgt = (rel.target or "").strip()
    if not src or not tgt:
        return None
    change_id = (rel.change_id or "").strip() or hashlib.md5(
        f"{src}|{tgt}|{rel.doc_id}|{rel.chapter_order}|{rel.summary}".encode(), usedforsecurity=False
    ).hexdigest()[:10]
    gid = f"{rel.doc_id or _slug(series_id)}_char_rel_{change_id}"
    chars = list(dict.fromkeys([src, tgt]))
    summary = (rel.summary or f"{src}与{tgt}：{rel.relation_type or '关系'}").strip()
    return NovelBlock(
        global_id=gid,
        doc_id=rel.doc_id or "",
        source=f"story_analysis:{series_id}",
        chapter_title=rel.chapter_title or "",
        block_type=BLOCK_CHARACTER,
        narrative_text=summary,
        characters=chars,
        all_person=chars,
        character_name=src,
        personality=rel.relation_type or "",
        speech_style=rel.polarity or "neutral",
        background=summary,
        relationships={
            "subtype": "relation",
            "source": src,
            "target": tgt,
            "relation_type": rel.relation_type or "",
            "polarity": rel.polarity or "neutral",
            "confidence": float(rel.confidence or 0),
            "series_id": series_id,
            "change_id": change_id,
            "chapter_order": int(rel.chapter_order or 0),  # 时间维度（P3）
        },
        ref_chunk_ids=evid,
        vec_text_character=relation_vec_text(rel, series_id=series_id),
        token_length=len(summary),
        granularity="relation",
        style_tags=["story_analysis", "relation"],
    )


def event_to_block(ev: StoryEvent, *, series_id: str) -> NovelBlock | None:
    evid = _evidence_ids(ev.evidence)
    if not evid:
        return None
    if float(ev.confidence or 0) < _MIN_CONFIDENCE:
        return None
    summary = (ev.summary or "").strip()
    if not summary:
        return None
    event_id = (ev.event_id or "").strip() or hashlib.md5(
        f"{ev.doc_id}|{ev.chapter_order}|{summary[:40]}".encode(), usedforsecurity=False
    ).hexdigest()[:10]
    gid = f"{ev.doc_id or _slug(series_id)}_char_evt_{event_id}"
    chars = [c for c in (ev.characters or []) if str(c).strip()]
    return NovelBlock(
        global_id=gid,
        doc_id=ev.doc_id or "",
        source=f"story_analysis:{series_id}",
        chapter_title=ev.chapter_title or "",
        block_type=BLOCK_CHARACTER,
        narrative_text=summary,
        characters=chars,
        all_person=chars,
        character_name=chars[0] if chars else "",
        personality=ev.event_type or "plot",
        speech_style="",
        background=summary,
        relationships={
            "subtype": "event",
            "event_type": ev.event_type or "plot",
            "confidence": float(ev.confidence or 0),
            "series_id": series_id,
            "event_id": event_id,
            "chapter_order": int(ev.chapter_order or 0),  # 时间维度（P3）
        },
        ref_chunk_ids=evid,
        vec_text_character=event_vec_text(ev, series_id=series_id),
        token_length=len(summary),
        granularity="event",
        style_tags=["story_analysis", "event"],
    )


def blocks_from_snapshot(snap: StoryAnalysisSnapshot) -> list[NovelBlock]:
    """Project a story-analysis snapshot into character-channel blocks."""
    series_id = snap.series_id or ""
    blocks: list[NovelBlock] = []
    for rel in snap.relations or []:
        b = relation_to_block(rel, series_id=series_id)
        if b:
            blocks.append(b)
    for ev in snap.events or []:
        b = event_to_block(ev, series_id=series_id)
        if b:
            blocks.append(b)
    return blocks


def looks_like_relation_question(text: str) -> bool:
    """Heuristic: user asks about relationships / plot events involving characters."""
    t = (text or "").strip()
    if not t:
        return False
    return bool(_RELATION_QUESTION_RE.search(t))


def entities_mentioned(text: str, known: Sequence[str]) -> list[str]:
    """Return known character names found in text (longest-first to reduce partials)."""
    names = sorted({str(n).strip() for n in known if str(n).strip()}, key=len, reverse=True)
    bag = text or ""
    found: list[str] = []
    for name in names:
        if name in bag and name not in found:
            found.append(name)
    return found


def format_relation_event_clue(block: Any, *, clip: int = 200) -> str:
    """Short clue line for prompts / retrieval formatting."""
    rel = getattr(block, "relationships", None) or {}
    subtype = ""
    if isinstance(rel, dict):
        subtype = str(rel.get("subtype") or "")
    subtype = subtype or str(getattr(block, "granularity", "") or "character")
    chars = [str(c) for c in (getattr(block, "characters", None) or []) if c]
    summary = (
        str(getattr(block, "narrative_text", "") or "")
        or str(getattr(block, "background", "") or "")
    ).strip()
    if clip > 0 and len(summary) > clip:
        summary = summary[:clip] + "…"
    ch = f" 角色:{'、'.join(chars)}" if chars else ""
    chapter = getattr(block, "chapter_title", "") or ""
    loc = f" 章:{chapter}" if chapter else ""
    return f"[{subtype}]{ch}{loc} {summary}".strip()


def is_relation_event_block(block: Any) -> bool:
    gran = str(getattr(block, "granularity", "") or "")
    if gran in _RELATION_SUBTYPES:
        return True
    rel = getattr(block, "relationships", None)
    if isinstance(rel, dict) and rel.get("subtype") in _RELATION_SUBTYPES:
        return True
    tags = getattr(block, "style_tags", None) or []
    return any(t in {"relation", "event", "cooccur", "story_analysis"} for t in tags)


def block_covers_entities(block: Any, entities: Sequence[str]) -> bool:
    """True if block mentions/covers all entities (for pair questions)."""
    names = [str(e).strip() for e in entities if str(e).strip()]
    if len(names) < 2:
        return True
    chars = [str(c) for c in (getattr(block, "characters", None) or []) if c]
    bag = " ".join(
        chars
        + [str(c) for c in (getattr(block, "all_person", None) or []) if c]
        + [str(getattr(block, "character_name", "") or "")]
        + [str(getattr(block, "narrative_text", "") or "")]
        + [str(getattr(block, "background", "") or "")]
    )
    for name in names:
        if name in bag:
            continue
        if any(name in c or c in name for c in chars):
            continue
        return False
    return True


async def delete_relation_event_blocks(
    store,
    *,
    doc_ids: Sequence[str] | None = None,
    series_id: str | None = None,
) -> int:
    """Remove previously indexed relation/event character blocks."""
    doc_set = {d for d in (doc_ids or []) if d}
    blocks = []
    if hasattr(store, "iter_blocks"):
        if doc_set:
            for did in doc_set:
                blocks.extend(store.iter_blocks(block_type=BLOCK_CHARACTER, doc_id=did) or [])
        else:
            blocks = store.iter_blocks(block_type=BLOCK_CHARACTER) or []
    ids: list[str] = []
    for b in blocks:
        if not is_relation_event_block(b):
            continue
        if series_id:
            src = str(getattr(b, "source", "") or "")
            rel = getattr(b, "relationships", None) or {}
            sid = rel.get("series_id") if isinstance(rel, dict) else ""
            if series_id not in src and sid != series_id:
                continue
        if doc_set and getattr(b, "doc_id", "") not in doc_set:
            continue
        gid = getattr(b, "global_id", "") or ""
        if gid:
            ids.append(gid)
    if not ids:
        return 0
    if hasattr(store, "delete_by_global_ids"):
        return await store.delete_by_global_ids(ids)
    # Fallback: best-effort per-id if only lance private API
    deleted = 0
    lance = getattr(store, "_lance", None)
    if lance is not None and hasattr(lance, "delete_by_global_ids"):
        return lance.delete_by_global_ids(ids)
    logger.warning("No delete_by_global_ids on store; skipped %d relation/event deletes", len(ids))
    return deleted


async def mark_series_cards_stale(series_id: str) -> int:
    """Mark built cards of a series as stale after story-analysis updates.

    P4: 关系事实源（story_analysis 快照）更新 → 相关卡片 stale →
    下次 build 时只轻量刷新关系视图（refresh_relations），不重建人设。
    """
    try:
        from src.domain.character_card import CharacterCard
        from src.domain.novel.character_roster import load_roster

        roster = load_roster(series_id)
        if not roster:
            return 0
        marked = 0
        for e in roster.characters or []:
            if not getattr(e, "has_card", False):
                continue
            card = CharacterCard.load_for_series(
                series_id, e.name, character_id=e.character_id or ""
            )
            if card is None:
                continue
            if not card.stale:
                card.stale = True
                CharacterCard.save_for_series(
                    series_id, e.name, card, character_id=e.character_id or ""
                )
                marked += 1
        if marked:
            logger.info(
                "Marked %d cards stale for series=%s (story analysis updated)",
                marked, series_id,
            )
        return marked
    except Exception as exc:  # noqa: BLE001
        logger.warning("mark_series_cards_stale(%s) failed: %s", series_id, exc)
        return 0


async def index_story_analysis(
    store,
    snap: StoryAnalysisSnapshot,
    *,
    replace: bool = True,
) -> dict[str, Any]:
    """Persist relation/event blocks for a story-analysis snapshot."""
    blocks = blocks_from_snapshot(snap)
    deleted = 0
    if replace:
        deleted = await delete_relation_event_blocks(
            store,
            doc_ids=list(snap.doc_ids or []),
            series_id=snap.series_id,
        )
    indexed = 0
    if blocks:
        indexed = await store.index_batch(blocks)
    # P4: 事实源更新后标记相关卡片 stale（下次 build 轻量刷新关系视图）
    await mark_series_cards_stale(snap.series_id)
    stats = {
        "deleted": deleted,
        "built": len(blocks),
        "indexed": indexed,
        "relations": sum(1 for b in blocks if b.granularity == "relation"),
        "events": sum(1 for b in blocks if b.granularity == "event"),
        "series_id": snap.series_id,
    }
    logger.info(
        "Character-channel story index: series=%s deleted=%d built=%d indexed=%d (rel=%d evt=%d)",
        snap.series_id,
        deleted,
        len(blocks),
        indexed,
        stats["relations"],
        stats["events"],
    )
    return stats
