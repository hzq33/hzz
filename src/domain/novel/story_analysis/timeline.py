"""Chronicle timeline builder — 编年体小说时间线（V5 世界体系 L2 层）。

从 story_analysis 快照（events + relations + story_time）派生编年体时间线：
  - ``chronicle``：主表，按故事内时间排序的事件序列（史书编年体式）
  - ``by_character``：角色索引（参与事件的 seq 列表，保持全局顺序）
  - ``by_era``：按阶段分组（可选）

排序双键：story_time.year（LLM 推断，有则用）→ chapter_order（章节序兜底）。
year 缺失的事件按章节序排，不强求精确时间。

消费方：
  - 角色设定集 V5（角色发展路线 = by_character 指向的 chronicle 序列）
  - Lorebook 生成（时间感知设定书）
  - 前端时间轴 / 检索时间窗过滤
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.domain.novel.story_analysis.models import (
    StoryAnalysisSnapshot,
    StoryEvent,
)

logger = logging.getLogger("agent")

from src.domain.novel.series_paths import data_root

_TIMELINE_DIR = data_root() / "timelines"

# 阶段枚举（无 year 时的相对顺序兜底）
_ERA_ORDER: dict[str, int] = {
    "转生前": 0,
    "转生": 1,
    "转生后": 2,
    "建国前": 3,
    "建国": 4,
    "建国后": 5,
    "魔王时期": 6,
    "大战": 7,
    "结局后": 8,
    "其他": 9,
}


def _sort_key(st: dict | None, *, doc_id: str = "", chapter_order: int = 0, fallback: str = "") -> tuple:
    """事件排序键：year → era → 章节序 三级兜底。"""
    st = st or {}
    y = st.get("year")
    if isinstance(y, int):
        return (0, y, doc_id, chapter_order, fallback)
    period = str(st.get("period") or "")
    era_idx = _ERA_ORDER.get(period, 99)
    if period:
        return (1, era_idx, doc_id, chapter_order, fallback)
    return (2, doc_id, chapter_order, fallback)


def _entity_aliases(series_id: str) -> dict[str, list[str]]:
    """从 alias_map 读实体别名（canonical → aliases 列表）。"""
    try:
        from src.domain.novel.alias_map import load_alias_map

        amap = load_alias_map(series_id)
        if amap is not None:
            out: dict[str, list[str]] = {}
            for e in getattr(amap, "entities", None) or []:
                cn = str(getattr(e, "canonical_name", "") or "").strip()
                if cn:
                    out.setdefault(cn, [])
                    for a in getattr(e, "aliases", None) or []:
                        a = str(a).strip()
                        if a and a != cn:
                            out[cn].append(a)
            return out
    except Exception as exc:  # noqa: BLE001
        logger.debug("alias map unavailable for timeline: %s", exc)
    return {}


def _match_entity(name: str, aliases_map: dict[str, list[str]]) -> list[str]:
    """把事件角色名映射到 canonical（含别名匹配）。"""
    name = (name or "").strip()
    if not name:
        return []
    hits = []
    for cn, als in aliases_map.items():
        if name == cn or name in als:
            hits.append(cn)
    return hits or [name]  # 无匹配时保留原名


def build_chronicle(
    snapshot: StoryAnalysisSnapshot,
    *,
    series_id: str = "",
    key_event_types: tuple[str, ...] = ("world", "plot"),
) -> dict[str, Any]:
    """构建编年体时间线。

    Args:
        snapshot: story_analysis 快照（events/relations + story_time）。
        series_id: 系列 id（用于读别名；空则跳过别名展开）。
        key_event_types: 标记为关键节点（milestone）的 event_type 集合。

    Returns:
        {"series_id", "updated_at", "chronicle": [...], "by_character": {...},
         "by_era": [...], "stats": {...}}
    """
    series_id = series_id or snapshot.series_id
    aliases_map = _entity_aliases(series_id) if series_id else {}

    events: list[StoryEvent] = list(snapshot.events or [])
    # 稳定排序（reduce 已按 story_time 排，这里再排一次保证幂等）
    events.sort(
        key=lambda e: _sort_key(e.story_time, doc_id=e.doc_id, chapter_order=e.chapter_order, fallback=e.event_id)
    )

    chronicle: list[dict] = []
    by_character: dict[str, list[int]] = defaultdict(list)
    era_seqs: dict[str, list[int]] = defaultdict(list)
    key_events = 0

    for seq, ev in enumerate(events, 1):
        st = ev.story_time or {}
        # 参与角色 → canonical（别名展开）
        chars: list[str] = []
        for c in ev.characters or []:
            matched = _match_entity(c, aliases_map)
            for m in matched:
                if m not in chars:
                    chars.append(m)
                    by_character[m].append(seq)
        entry = {
            "seq": seq,
            "summary": ev.summary,
            "event_type": ev.event_type,
            "characters": chars,
            "confidence": round(float(ev.confidence or 0), 3),
            "doc_id": ev.doc_id,
            "chapter_order": ev.chapter_order,
            "chapter_title": ev.chapter_title,
            "evidence": [x.snippet for x in ev.evidence[:3]],
            "story_time": st,
        }
        is_key = ev.event_type in key_event_types
        entry["key_event"] = is_key
        if is_key:
            key_events += 1
        chronicle.append(entry)

        period = str(st.get("period") or "").strip()
        if period:
            era_seqs[period].append(seq)

    by_era = [
        {"era": period, "seqs": seqs, "events_count": len(seqs)}
        for period, seqs in sorted(era_seqs.items(), key=lambda kv: _ERA_ORDER.get(kv[0], 99))
    ]

    return {
        "series_id": series_id,
        "updated_at": datetime.now(UTC).isoformat(),
        "chronicle": chronicle,
        "by_character": {k: v for k, v in sorted(by_character.items())},
        "by_era": by_era,
        "stats": {
            "event_count": len(chronicle),
            "key_event_count": key_events,
            "character_count": len(by_character),
            "era_count": len(by_era),
            "year_annotated": sum(1 for e in chronicle if isinstance((e.get("story_time") or {}).get("year"), int)),
        },
    }


def load_timeline(series_id: str) -> dict[str, Any] | None:
    """读已落盘的编年体时间线。"""
    path = _TIMELINE_DIR / f"{series_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Timeline load failed for %s: %s", series_id, exc)
        return None


def save_timeline(series_id: str, data: dict[str, Any]) -> Path:
    """落盘编年体时间线到 data/timelines/{series}.json。"""
    _TIMELINE_DIR.mkdir(parents=True, exist_ok=True)
    path = _TIMELINE_DIR / f"{series_id}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def build_and_save(
    snapshot: StoryAnalysisSnapshot,
    *,
    series_id: str = "",
) -> dict[str, Any]:
    """构建并落盘编年体时间线（便捷入口）。"""
    data = build_chronicle(snapshot, series_id=series_id)
    save_timeline(data["series_id"], data)
    return data
