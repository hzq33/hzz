"""Relation store — single source of truth for character relationships.

读取 story_analysis 快照（StoryAnalysisSnapshot JSON），对外提供：
- 按角色查询其所有关系（作为 source 或 target）
- 关系摘要格式化（注入建卡 distill prompt / 卡片关系视图）

设计：事实源 = story_analysis 快照（含 RelationChange + evidence 锚定）；
卡片 / 图谱 / 检索都是该事实源的投影，此处是只读查询封装，不复制数据。
"""

from __future__ import annotations

import logging
from typing import Any

from src.domain.novel.story_analysis.models import RelationChange, StoryAnalysisSnapshot

logger = logging.getLogger("agent.relation_store")

# 关系摘要截断
_SUMMARY_CLIP = 120
_MAX_RELATIONS_PER_SIDE = 8


def load_snapshot(series_id: str) -> StoryAnalysisSnapshot | None:
    """Load the series story-analysis snapshot (the relation fact source)."""
    try:
        from src.domain.novel.story_analysis.config import load_analysis

        return load_analysis(series_id)
    except Exception as exc:  # noqa: BLE001
        logger.debug("load_snapshot(%s) failed: %s", series_id, exc)
        return None


def _norm_name_set(name: str, aliases: list[str] | None) -> set[str]:
    out = {n for n in (name or "").split("、") if n}
    for a in aliases or []:
        a = (a or "").strip()
        if len(a) >= 2:
            out.add(a)
    return out


def _rel_mentions(rel: RelationChange, name_set: set[str], *, match_summary: bool = True) -> bool:
    """True if a relation involves any of the given names (either side)."""
    for n in name_set:
        if n and (n in (rel.source or "") or n in (rel.target or "")):
            return True
    # 宽松模式：summary 提到也算（检索召回导向）；
    # 卡片视图用严格模式（match_summary=False），只认关系双方。
    if match_summary:
        for n in name_set:
            if n and n in (rel.summary or ""):
                return True
    return False


def _counterpart(rel: RelationChange, name_set: set[str]) -> str:
    """Return the OTHER side of a relation relative to the given names."""
    for n in name_set:
        if n and n in (rel.target or ""):
            return rel.source or ""
    for n in name_set:
        if n and n in (rel.source or ""):
            return rel.target or ""
    return ""


def relations_for_character(
    series_id: str,
    canonical_name: str,
    aliases: list[str] | None = None,
    *,
    include_self: bool = False,
    match_summary: bool = True,
) -> list[RelationChange]:
    """Return relations where the character appears as source or target.

    ``match_summary=False`` 时只认关系双方（source/target 命中），
    summary 提到不算——供卡片关系视图使用（避免"summary 提到但非
    关系双方"的条目污染视图）。

    Dedup: for the same counterpart, keep the most recent (max chapter_order)
    relation per counterpart, unless include_self keeps evolution history.
    """
    snap = load_snapshot(series_id)
    if not snap or not snap.relations:
        return []
    names = _norm_name_set(canonical_name, aliases)
    if not names:
        return []
    hits = [
        r for r in snap.relations
        if _rel_mentions(r, names, match_summary=match_summary)
    ]
    if not hits:
        return []
    if include_self:
        hits.sort(key=lambda r: (r.doc_id or "", r.chapter_order))
        return hits
    # 每个 counterpart 只保留最新一条（当前关系状态）
    latest: dict[str, RelationChange] = {}
    for r in hits:
        counterpart = _counterpart(r, names) or r.change_id
        cur = latest.get(counterpart)
        if cur is None or (r.doc_id or "", r.chapter_order) >= (cur.doc_id or "", cur.chapter_order):
            latest[counterpart] = r
    return sorted(latest.values(), key=lambda r: (-float(r.confidence or 0), r.chapter_order))


def format_relation_summary(rel: RelationChange, *, clip: int = _SUMMARY_CLIP) -> str:
    """Single relation → one-line summary with polarity/confidence/evidence."""
    typ = (rel.relation_type or "").strip() or "未标注"
    pol = (rel.polarity or "neutral").strip()
    conf = float(rel.confidence or 0)
    ev = len(rel.evidence or [])
    loc = f"（{rel.chapter_title or rel.doc_id or ''}）" if (rel.chapter_title or rel.doc_id) else ""
    summary = (rel.summary or "").strip()
    if clip > 0 and len(summary) > clip:
        summary = summary[:clip] + "…"
    line = f"{rel.source} ↔ {rel.target}：{typ}（{pol}，置信{conf:.0%}，证据{ev}条）{loc}"
    if summary:
        line += f" | {summary}"
    return line


def format_relations_block(
    relations: list[RelationChange],
    *,
    clip: int = _SUMMARY_CLIP,
    max_rels: int = _MAX_RELATIONS_PER_SIDE,
) -> str:
    """Relations list → prompt-ready text block."""
    if not relations:
        return ""
    lines = [format_relation_summary(r, clip=clip) for r in relations[:max_rels]]
    return "\n".join(lines)


def relations_view_for_card(
    series_id: str,
    canonical_name: str,
    aliases: list[str] | None = None,
    *,
    max_rels: int = 10,
) -> list[dict[str, Any]]:
    """Project relations into the lightweight card view (structured dicts).

    Each entry: counterpart / relation_type / category / polarity /
    confidence / evidence_count / chapter_title / change_id.
    Used by CharacterCard.relations_view and to_prompt().
    """
    rels = relations_for_character(
        series_id, canonical_name, aliases, match_summary=False
    )
    names = _norm_name_set(canonical_name, aliases)
    out: list[dict[str, Any]] = []
    for r in rels[:max_rels]:
        counterpart = _counterpart(r, names)
        out.append(
            {
                "name": counterpart or (r.source if r.source != canonical_name else r.target),
                "relation_type": (r.relation_type or "").strip(),
                "category": "",  # 由 relation_graph.classify 聚合时填充；此处保留 LLM 原文
                "polarity": (r.polarity or "neutral").strip(),
                "confidence": round(float(r.confidence or 0), 3),
                "evidence_count": len(r.evidence or []),
                "chapter_title": (r.chapter_title or "").strip(),
                "change_id": r.change_id,
            }
        )
    return out
