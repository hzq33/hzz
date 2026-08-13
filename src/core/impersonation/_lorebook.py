"""Temporal Lorebook activator — 时间感知设定书激活器（V5 P3）。

在扮演聊天时，根据用户消息关键词 + 当前故事时间，激活 Lorebook 条目注入
system prompt（酒馆式动态注入）。

激活规则：
  1. 关键词命中：用户消息（含当前角色名）含 entry.keys 任一 → 候选
  2. 时间匹配：current_time（当前故事 era/year）在 entry.time_range 内 → 激活；
     无 current_time 或条目无时间限制 → 全部候选激活
  3. 优先级排序：priority 高者先；同类按 seq_from 排序
  4. 注入格式：system prompt 追加「[世界设定] era 标签：content」
"""

from __future__ import annotations

import logging

logger = logging.getLogger("agent")

# 注入上限（防上下文膨胀）
_MAX_ACTIVE_ENTRIES = 6
# 单条注入内容上限（字符）
_MAX_CONTENT_CHARS = 200


def _match_time(entry: dict, current_time: dict | None) -> bool:
    """时间窗口匹配：current_time 在 entry.time_range 内。

    current_time: {"era": str|None, "year": int|None}
    """
    tr = entry.get("time_range") or {}
    if current_time is None:
        return True
    era = str(current_time.get("era") or "").strip()
    year = current_time.get("year")
    if era and tr.get("era"):
        # era 精确匹配（含空 era = 不限）
        if str(tr.get("era")) == era:
            return True
        # entry 无 era 限制 → 匹配
        if not str(tr.get("era")) or str(tr.get("era")) == "其他":
            return True
        return False
    if isinstance(year, int):
        yf = tr.get("year_from")
        yt = tr.get("year_to")
        if yf is not None and year < int(yf):
            return False
        if yt is not None and year > int(yt):
            return False
        return True
    # 无 current_time 信息 → 放行（保守注入）
    return True


def activate_entries(
    entries: list[dict],
    *,
    user_input: str,
    character: str = "",
    current_time: dict | None = None,
    max_entries: int = _MAX_ACTIVE_ENTRIES,
) -> list[dict]:
    """按关键词 + 时间激活条目，按优先级排序返回。

    Args:
        entries: Lorebook 条目列表（data/lorebooks/{series}.json 的 entries）。
        user_input: 用户消息。
        character: 当前扮演角色（其名也参与关键词匹配）。
        current_time: 当前故事时间 {"era": str|None, "year": int|None}；None = 不限。
        max_entries: 激活上限。

    Returns:
        激活的条目列表（含命中关键词 info）。
    """
    if not entries:
        return []
    text = f"{user_input or ''} {character or ''}"
    hits: list[tuple[dict, str]] = []
    for entry in entries:
        if not entry.get("active", True):
            continue
        keys = entry.get("keys") or []
        matched = None
        for k in keys:
            k = str(k).strip()
            if k and k in text:
                matched = k
                break
        if matched is None:
            continue
        if not _match_time(entry, current_time):
            continue
        hits.append((entry, matched))
    # 排序：priority 高者先 → seq_from 小者先
    hits.sort(
        key=lambda x: (
            -int(x[0].get("priority") or 0),
            int(x[0].get("seq_from") or 0),
        )
    )
    out = []
    for entry, matched in hits[:max_entries]:
        item = dict(entry)
        item["matched_key"] = matched
        out.append(item)
    return out


def format_entries_block(entries: list[dict]) -> str:
    """激活条目 → 注入文本块（带 era 标签）。"""
    if not entries:
        return ""
    lines = ["## 世界设定（当前时间线）"]
    for e in entries:
        era = ((e.get("time_range") or {}).get("era") or "").strip()
        tag = f"[{era}]" if era else ""
        content = str(e.get("content") or "").strip()
        if len(content) > _MAX_CONTENT_CHARS:
            content = content[:_MAX_CONTENT_CHARS] + "…"
        lines.append(f"- {tag} {content}")
    return "\n".join(lines)


def load_lorebook_entries(series_id: str) -> list[dict]:
    """读系列设定书条目（失败回退空）。"""
    if not series_id:
        return []
    try:
        from src.application.novel.services.story_analysis_service import load_lorebook

        data = load_lorebook(series_id)
        return list((data or {}).get("entries") or [])
    except Exception as exc:  # noqa: BLE001
        logger.debug("lorebook load failed for %s: %s", series_id, exc)
        return []


# era 关键词 → 故事时段（从用户消息/历史检测）
# 注意顺序：同前缀更长的关键词在前（"建国后" 必须先于 "建国"），
# 匹配时按词长降序，避免 "建国后" 被 "建国" 抢先命中。
_ERA_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("转生前", ("转生前", "前世", "上辈子", "死前", "生前", "以前的世界", "原来世界")),
    ("转生后", ("转生后", "刚转生", "转生过后", "初到异世界", "刚到异世界", "刚来异世界")),
    ("建国前", ("建国前", "建国之前", "建村前", "村庄时期", "村子时期")),
    ("建国", ("建国", "建立国家", "成立国家", "建都")),
    ("建国后", ("建国后", "建国之后", "国家建立后", "建成之后", "魔国联邦", "现在的时间线")),
    ("魔王时期", ("魔王时期", "成为魔王", "晋升魔王", "魔王时代")),
    ("大战", ("大战", "战争时期", "决战", "开战")),
    ("大战后", ("大战后", "战后", "战争结束", "大战之后", "战后重建")),
    ("结局后", ("结局", "大结局", "完结后", "结局之后")),
]


# 所有关键词按长度降序（长词优先匹配，如 "建国后" > "建国"）
_ALL_KW: list[tuple[str, str]] = sorted(
    ((kw, era) for era, kws in _ERA_KEYWORDS for kw in kws),
    key=lambda x: (-len(x[0]), x[0]),
)


def infer_current_time(text: str) -> dict | None:
    """从文本（用户消息 + 历史）检测当前故事时段。

    长关键词优先（避免 "建国后" 被 "建国" 抢先）；返回 {"era": str, "year": None}；
    未命中 → None（全时段注入）。
    """
    if not text:
        return None
    # 收集所有命中（词长降序已排序），取最长的那个（词越长越精确）
    best: tuple[int, str] | None = None
    for kw, era in _ALL_KW:
        idx = text.find(kw)
        if idx >= 0:
            # 词长优先；等长取位置靠后（最近的表达）
            if best is None or len(kw) > best[0]:
                best = (len(kw), era)
    if best is None:
        return None
    return {"era": best[1], "year": None}
