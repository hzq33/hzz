"""WorldKnowledgeTool — 结构化世界知识查询工具。

查询系列的结构化世界知识（角色关系 / 事件 / 时间线 / 设定书 / 角色事件线），
数据来自剧情分析产物（story_analyses / timelines / lorebooks），索引于 SQLite。

设计原则（docs/WORLD_KNOWLEDGE_TOOL_DESIGN.md v5）：
- 返回行级摘要 + evidence block_id 指针（不展开原文）——原文由 novel_search 取
- 精确过滤（SQL），查出的即相关；价值判断由 LLM 后处理完成并回收为评估指标
- 不生成任何 LLM 内容，只搬运已有结构化字段
"""

from __future__ import annotations

import json
from typing import Any

from src.tools.base import BaseTool, ToolResult

_QUERY_TYPES = [
    "relations",          # 角色关系：谁和谁什么关系
    "events",             # 事件：发生了什么
    "timeline",           # 时间线：按顺序的事件流（可按时代过滤）
    "lorebook",           # 设定书：实体/设定条目
    "character_events",   # 角色事件线：某角色的全部事件 seq
]


class WorldKnowledgeTool(BaseTool):
    name: str = "world_knowledge"
    description: str = (
        "查询小说的结构化世界知识（角色关系/事件/时间线/设定书）。\n"
        "当问题涉及「谁和谁什么关系」「后来发生了什么」「某个时期/时代发生了什么」\n"
        "「某某是谁/是什么/设定」时调用；比 novel_search 更精准（结构化数据）。\n"
        "返回条目级摘要+证据定位，需要原文时再用 novel_search 取证据。\n"
        "query_type 说明：\n"
        "  - relations: 角色关系（entity=角色A，entity2=角色B 可查两人关系）\n"
        "  - events: 事件（可按 era 过滤，如 转生前/转生后）\n"
        "  - timeline: 按序事件流（era 过滤）\n"
        "  - lorebook: 设定书条目（entity=实体名）\n"
        "  - character_events: 某角色的全部事件（entity=角色名）\n"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query_type": {
                "type": "string",
                "enum": _QUERY_TYPES,
                "description": "查询类型",
            },
            "series_id": {
                "type": "string",
                "description": "作品/系列名（必填，如「关于我转生变成史莱姆这档事」）",
            },
            "entity": {
                "type": "string",
                "description": "角色/实体名（relations 的参与方、events/timeline 的涉及角色、lorebook 的实体）",
            },
            "entity2": {
                "type": "string",
                "description": "关系对第二方（仅 query_type=relations 用，查两人关系）",
            },
            "relation_type": {
                "type": "string",
                "description": "关系类型过滤（仅 relations：敌对/情侣/主从/合作…）",
            },
            "era": {
                "type": "string",
                "description": "时代过滤（如 转生前/转生后；仅 events/timeline）",
            },
            "limit": {
                "type": "integer",
                "description": "返回条数上限（1-20，默认 10）",
                "default": 10,
            },
        },
        "required": ["query_type", "series_id"],
    }

    async def execute(self, **kwargs: Any) -> ToolResult:
        query_type = str(kwargs.get("query_type") or "").strip()
        series_id = str(kwargs.get("series_id") or "").strip()
        entity = (str(kwargs.get("entity") or "").strip()) or None
        entity2 = (str(kwargs.get("entity2") or "").strip()) or None
        relation_type = (str(kwargs.get("relation_type") or "").strip()) or None
        era = (str(kwargs.get("era") or "").strip()) or None
        limit = int(kwargs.get("limit") or 10)

        if not series_id:
            return ToolResult.fail("world_knowledge 需要 series_id（作品/系列名）")
        if query_type not in _QUERY_TYPES:
            return ToolResult.fail(
                f"query_type 必须是 {'/'.join(_QUERY_TYPES)} 之一"
            )

        try:
            from src.application.novel.services import world_knowledge_service as svc
        except Exception as exc:  # noqa: BLE001
            return ToolResult.fail(f"World knowledge service unavailable: {exc}")

        try:
            if query_type == "relations":
                rows = svc.query_relations(
                    series_id, entity=entity, entity2=entity2,
                    relation_type=relation_type, era=era, limit=limit,
                )
            elif query_type == "events":
                rows = svc.query_events(series_id, entity=entity, era=era, limit=limit)
            elif query_type == "timeline":
                rows = svc.query_timeline(series_id, era=era, limit=limit)
            elif query_type == "character_events":
                rows = svc.query_character_events(series_id, character=entity, limit=limit)
            else:  # lorebook
                rows = svc.query_lorebook(series_id, entity=entity, limit=limit)
        except Exception as exc:  # noqa: BLE001
            return ToolResult.fail(f"World knowledge query failed: {exc}")

        if not rows:
            # 确定性回收：空结果 → useless（不依赖 LLM verdict，扮演场景兜底）
            try:
                from src.shared.metrics import observe_tool_value

                observe_tool_value(query_type=query_type, verdict="useless")
            except Exception:  # noqa: BLE001
                pass
            return ToolResult.ok(
                f"该系列「{series_id}」无匹配的{query_type}数据"
                "（可能未做剧情分析，或条件无命中）。"
            )

        # 确定性回收：有结果 → valuable
        try:
            from src.shared.metrics import observe_tool_value

            observe_tool_value(query_type=query_type, verdict="valuable")
        except Exception:  # noqa: BLE001
            pass
        lines = [f"## 世界知识（{query_type}）— {series_id}", ""]
        for row in rows:
            lines.append(_format_row(query_type, row))
            lines.append("")
        return ToolResult.ok("\n".join(lines))


def _format_row(query_type: str, row: dict) -> str:
    """单行格式化：行级摘要 + 证据 block_id 指针（不展开原文）。"""
    chapter = row.get("chapter_title") or f"第{row.get('chapter_order')}章"
    period = row.get("story_period") or ""
    where = f"{chapter}" + (f"（{period}）" if period else "")

    if query_type == "relations":
        ev = _first_evidence(row.get("evidence_ids"))
        line = (
            f"· {row.get('source')} —[{row.get('relation_type')}"
            f"/{row.get('polarity')}]→ {row.get('target')} ｜ {where}"
        )
        if row.get("summary"):
            line += f"\n  {row.get('summary')}"
        if ev:
            line += f"\n  证据: {ev}"
        return line

    if query_type == "events":
        ev = _first_evidence(row.get("evidence_ids"))
        chars = _json_list(row.get("characters"))
        line = f"· [{period}] {row.get('summary')} ｜ {where}"
        if chars:
            line += f"\n  角色: {', '.join(chars)}"
        if ev:
            line += f"\n  证据: {ev}"
        return line

    if query_type == "timeline":
        chars = _json_list(row.get("characters"))
        line = f"· #{row.get('seq')} [{period}] {row.get('summary')} ｜ {where}"
        if chars:
            line += f"\n  角色: {', '.join(chars)}"
        return line

    if query_type == "character_events":
        seqs = _json_list(row.get("seqs"))
        return (
            f"· {row.get('character')}: 参与事件 seq "
            f"{', '.join(str(s) for s in seqs) if seqs else '无'}"
        )

    # lorebook
    keys = _json_list(row.get("keys"))
    line = (
        f"· {row.get('entity')}（{row.get('kind')}）seq "
        f"{row.get('seq_from')}-{row.get('seq_to')} ｜ {where}"
    )
    if keys:
        line += f"\n  别名: {', '.join(keys)}"
    content = str(row.get("content") or "")
    if content:
        line += f"\n  {content[:200]}"
    return line


def _json_list(raw: str | None) -> list[str]:
    try:
        v = json.loads(raw or "[]")
        return list(v) if isinstance(v, list) else []
    except Exception:  # noqa: BLE001
        return []


def _first_evidence(raw: str | None) -> str:
    try:
        v = json.loads(raw or "[]")
        return str(v[0]) if isinstance(v, list) and v else ""
    except Exception:  # noqa: BLE001
        return ""
