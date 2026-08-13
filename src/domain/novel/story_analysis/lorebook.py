"""Temporal Lorebook builder — 时间感知设定书（V5 世界体系 L3 前置，P2）。

从编年体时间线（chronicle）+ 实体名单（inventory）派生"酒馆式"设定条目：
  - 实体条目：每实体按故事阶段（era/period）分段，每段一条
  - 关系条目：双实体同框事件 → 关系演变条目
  - 每条带 keys（触发关键词）/ time_range（生效时间窗）/ priority / content

激活方式（P3 扮演注入）：
  聊天消息含 keys → 候选；当前故事时间在 time_range 内 → 激活注入 system prompt。

产物：data/lorebooks/{series}.json
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("agent")

from src.domain.novel.series_paths import data_root

_LOREBOOK_DIR = data_root() / "lorebooks"

# era 相对顺序（同 timeline._ERA_ORDER）
_ERA_ORDER: dict[str, int] = {
    "转生前": 0, "转生": 1, "转生后": 2, "建国前": 3, "建国": 4,
    "建国后": 5, "魔王时期": 6, "大战": 7, "大战后": 8, "结局后": 9, "其他": 10,
}


def _era_rank(period: str) -> int:
    return _ERA_ORDER.get(period or "", 99)


def _load_inventory(series_id: str) -> dict[str, dict]:
    """读 inventory candidates：name → {aliases, attributes, importance, mention_count}。"""
    from src.domain.novel.character_inventory.candidates import inventory_path

    path = inventory_path(series_id)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("inventory load failed for %s: %s", series_id, exc)
        return {}
    out: dict[str, dict] = {}
    for c in data.get("candidates") or []:
        name = str(c.get("name") or "").strip()
        if not name:
            continue
        out[name] = {
            "aliases": [str(a) for a in (c.get("aliases") or []) if str(a).strip()],
            "attributes": list(c.get("attributes") or []),
            "importance": str(c.get("importance") or "extra"),
            "mention_count": int(c.get("mention_count") or 0),
        }
    return out


def _load_timeline(series_id: str) -> dict[str, Any] | None:
    from src.domain.novel.story_analysis.timeline import load_timeline

    return load_timeline(series_id)


def _entity_keys(name: str, info: dict) -> list[str]:
    """实体触发关键词：canonical + aliases。"""
    keys = [name]
    for a in info.get("aliases") or []:
        a = str(a).strip()
        if a and a not in keys:
            keys.append(a)
    return keys


def _time_range_from_period(period: str) -> dict:
    """由 period 生成 time_range（year 未知，用 era 标记）。"""
    return {"year_from": None, "year_to": None, "era": period or ""}


def _format_attributes(attrs: list[dict]) -> str:
    """属性列表 → 人读文本（"种族：矮人；身份：武器锻造师"）。"""
    if not attrs:
        return ""
    parts = []
    label = {"race": "种族", "role": "身份", "title": "称号",
             "location": "地点", "org": "组织", "skill_attr": "能力", "item": "物品"}
    for a in attrs:
        t = str(a.get("type") or "").strip()
        v = str(a.get("value") or "").strip()
        if t and v:
            parts.append(f"{label.get(t, t)}：{v}")
    return "；".join(parts)


def build_lorebook(
    series_id: str,
    *,
    timeline: dict[str, Any] | None = None,
    inventory: dict[str, dict] | None = None,
    max_entities: int = 40,
    min_events_per_entity: int = 2,
) -> dict[str, Any]:
    """构建时间感知设定书。

    Args:
        series_id: 系列 id。
        timeline: 编年体时间线（缺省自动加载 data/timelines/{series}.json）。
        inventory: 实体名单（缺省自动加载 data/inventories/{series}.json）。
        max_entities: 最多为多少个实体生成条目（按 mention 排序取前 N）。
        min_events_per_entity: 实体至少参与多少事件才生成实体条目（过滤路人）。

    Returns:
        {"series_id", "updated_at", "entries": [...], "stats": {...}}
    """
    if timeline is None:
        timeline = _load_timeline(series_id) or {}
    if inventory is None:
        inventory = _load_inventory(series_id)

    chronicle: list[dict] = timeline.get("chronicle") or []
    by_character: dict[str, list[int]] = timeline.get("by_character") or {}

    entries: list[dict] = []
    seq_by_id = {e["seq"]: e for e in chronicle}

    # ── 1. 实体条目：按故事阶段分段 ──
    # 实体 → 参与的事件 → 按 period 分组 → 每段一条 entry
    entity_periods: dict[str, dict[str, list[int]]] = {}
    for name, seqs in by_character.items():
        periods: dict[str, list[int]] = {}
        for s in seqs:
            ev = seq_by_id.get(s) or {}
            st = ev.get("story_time") or {}
            period = str(st.get("period") or "其他").strip() or "其他"
            periods.setdefault(period, []).append(s)
        entity_periods[name] = periods

    # 按 mention 排序取前 N 实体
    ranked_entities = sorted(
        inventory.keys(),
        key=lambda n: -(inventory[n].get("mention_count") or 0),
    )
    for name in ranked_entities[:max_entities]:
        seqs = by_character.get(name) or []
        if len(seqs) < min_events_per_entity:
            continue
        periods = entity_periods.get(name) or {}
        for period, pseqs in sorted(
            periods.items(), key=lambda kv: _era_rank(kv[0])
        ):
            pseqs_sorted = sorted(pseqs)
            # 该时段事件摘要（拼接，控制长度）
            summaries = []
            for s in pseqs_sorted:
                ev = seq_by_id.get(s) or {}
                sm = str(ev.get("summary") or "").strip()
                if sm and sm not in summaries:
                    summaries.append(sm)
            # 该时段属性状态（从 inventory 全量属性，后续可细化到时间段）
            attrs_txt = _format_attributes(inventory[name].get("attributes") or [])
            content = f"{name}"
            if attrs_txt:
                content += f"（{attrs_txt}）"
            if summaries:
                content += "。" + "；".join(summaries[:3])
            entries.append(
                {
                    "entry_id": f"lb_{name}_{period}",
                    "kind": "entity",
                    "entity": name,
                    "keys": _entity_keys(name, inventory[name]),
                    "time_range": _time_range_from_period(period),
                    "seq_from": min(pseqs_sorted),
                    "seq_to": max(pseqs_sorted),
                    "priority": 30,
                    "content": content,
                    "source": "chronicle",
                    "active": True,
                }
            )

    # ── 2. 关系条目：双实体同框事件 → 关系演变（同事件只生成 1 条，避免重复）──
    # 每个 chronicle 事件生成 1 条关系条目（若事件含 ≥2 角色），keys 含全部参与者；
    # 不再按 (a,b,period) 拆分对——避免同事件派生 N 条内容相同的条目。
    event_relation_entries: dict[int, dict] = {}
    for ev in chronicle:
        chars = [str(c) for c in (ev.get("characters") or []) if str(c).strip()]
        if len(chars) < 2:
            continue
        st = ev.get("story_time") or {}
        period = str(st.get("period") or "其他").strip() or "其他"
        seq = int(ev.get("seq") or 0)
        if not seq:
            continue
        sm = str(ev.get("summary") or "").strip()
        # 该事件所有参与者两两组合的实体对（用于 keys 触发）
        pair_keys = []
        for i in range(len(chars)):
            for j in range(i + 1, len(chars)):
                pair_keys.append(f"{chars[i]}与{chars[j]}")
                pair_keys.append(f"{chars[i]}和{chars[j]}")
        entry = {
            "entry_id": f"lb_rel_ev{seq}",
            "kind": "relation",
            "entity": chars[0],
            "counterpart": chars[1] if len(chars) > 1 else "",
            "keys": sorted(set([*chars, *pair_keys])),
            "time_range": _time_range_from_period(period),
            "seq_from": seq,
            "seq_to": seq,
            "priority": 15,
            "content": f"{chars[0]} 与 {'、'.join(chars[1:])}" + (f"：{sm}" if sm else ""),
            "source": "chronicle",
            "active": True,
        }
        event_relation_entries[seq] = entry
    entries.extend(event_relation_entries.values())

    # 排序：kind(entity 优先) → 时间 → priority
    entries.sort(
        key=lambda e: (0 if e["kind"] == "entity" else 1, _era_rank((e["time_range"] or {}).get("era") or ""), e["seq_from"])
    )
    return {
        "series_id": series_id,
        "updated_at": datetime.now(UTC).isoformat(),
        "entries": entries,
        "stats": {
            "entity_entries": sum(1 for e in entries if e["kind"] == "entity"),
            "relation_entries": sum(1 for e in entries if e["kind"] == "relation"),
            "entity_count": len(by_character),
            "event_count": len(chronicle),
        },
    }


def lorebook_path(series_id: str) -> Path:
    return _LOREBOOK_DIR / f"{series_id}.json"


def load_lorebook(series_id: str) -> dict[str, Any] | None:
    path = lorebook_path(series_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("lorebook load failed for %s: %s", series_id, exc)
        return None


def save_lorebook(series_id: str, data: dict[str, Any]) -> Path:
    _LOREBOOK_DIR.mkdir(parents=True, exist_ok=True)
    path = lorebook_path(series_id)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def build_and_save(series_id: str) -> dict[str, Any]:
    """构建并落盘设定书（便捷入口）。"""
    data = build_lorebook(series_id)
    save_lorebook(series_id, data)
    return data
