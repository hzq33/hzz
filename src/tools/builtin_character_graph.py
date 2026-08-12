"""角色关系图谱工具 — 读取 story_analysis 聚合出的角色关系图（读）。

与前端「世界体系 → 关系图」tab 同源：将剧情分析中的关系变化记录聚合为
角色节点 + 边（weight=出现次数，category=主流关系类型）。数据来自
story_analysis，未分析时提示先 build。
"""

from __future__ import annotations

import logging
from typing import Any

from src.tools.base import BaseTool, ToolResult

logger = logging.getLogger("agent")


class CharacterGraphTool(BaseTool):
    """读取角色关系图谱：节点、边、统计（读，不写）。"""

    name: str = "character_graph"
    description: str = (
        "角色关系图谱：角色节点 + 关系边（谁和谁有什么关系、边权重/类型）。\n"
        "当问题涉及「角色之间的关系网络」「谁和谁认识/敌对/合作」「关系强度」时调用；\n"
        "需要 series_id；可选 doc_id 锁单卷、min_confidence/min_weight 过滤。\n"
        "数据来自剧情分析（story_analysis），未分析时提示先 build。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "series_id": {
                "type": "string",
                "description": "系列 ID（如「败犬女主太多了」）",
            },
            "doc_id": {
                "type": "string",
                "description": "可选：限定单卷",
            },
            "min_confidence": {
                "type": "number",
                "description": "边的最低置信度（0-1，默认 0）",
                "default": 0.0,
            },
            "min_weight": {
                "type": "integer",
                "description": "边的最低出现次数（默认 1）",
                "default": 1,
            },
            "top_nodes": {
                "type": "integer",
                "description": "最多返回节点数（默认 20，0=全部）",
                "default": 20,
            },
        },
        "required": ["series_id"],
    }

    async def execute(self, **kwargs: Any) -> ToolResult:
        try:
            self.validate_args(kwargs)
            series_id = (kwargs.get("series_id") or "").strip()
            if not series_id:
                return ToolResult.fail("character_graph 需要 series_id（系列名）")
            doc_id = (kwargs.get("doc_id") or "").strip() or None
            min_confidence = float(kwargs.get("min_confidence") or 0.0)
            min_weight = int(kwargs.get("min_weight") or 1)
            top_nodes = int(kwargs.get("top_nodes") or 20)

            try:
                from src.application.novel.services.story_analysis_service import (
                    build_relation_graph,
                    load_analysis,
                )
            except Exception as exc:  # noqa: BLE001
                return ToolResult.fail(f"story_analysis service unavailable: {exc}")

            snapshot = load_analysis(series_id)
            if not snapshot:
                return ToolResult.ok(
                    f"系列「{series_id}」尚无剧情分析。可用 story_analysis action=build 生成。"
                )
            relations = list(snapshot.relations or [])
            if doc_id:
                relations = [r for r in relations if getattr(r, "doc_id", None) == doc_id]
            graph = build_relation_graph(
                relations,
                min_confidence=min_confidence,
                min_weight=min_weight,
            )
            nodes = graph.get("nodes") or []
            edges = graph.get("edges") or []
            stats = graph.get("stats") or {}

            lines = [
                f"## 角色关系图谱 — {series_id}",
                f"节点={len(nodes)} 边={len(edges)} "
                f"(min_conf={min_confidence} min_weight={min_weight})",
            ]
            if doc_id:
                lines[1] += f" doc_id={doc_id}"
            if stats:
                lines.append(f"统计: {stats}")
            lines.append("")

            if top_nodes > 0:
                nodes = nodes[:top_nodes]
            if nodes:
                lines.append("### 节点")
                for n in nodes:
                    name = n.get("name") or n.get("id") or "?"
                    degree = n.get("degree") or ""
                    lines.append(
                        f"- {name}" + (f" | degree={degree}" if degree else "")
                    )
            if edges:
                lines.append("")
                lines.append("### 边（角色对 → 关系类型 × 权重）")
                for e in edges[:50]:
                    src = e.get("source") or "?"
                    tgt = e.get("target") or "?"
                    cat = e.get("category") or e.get("relation_type") or "?"
                    weight = e.get("weight") or e.get("count") or 1
                    conf = e.get("confidence")
                    extra = f" conf={conf:.2f}" if isinstance(conf, (int, float)) else ""
                    lines.append(f"- {src} —[{cat}×{weight}{extra}]→ {tgt}")
                if len(edges) > 50:
                    lines.append(f"…另有 {len(edges) - 50} 条边未列出")
            return ToolResult.ok("\n".join(lines))
        except ValueError as e:
            return ToolResult.fail(str(e))
        except Exception as e:
            logger.exception("CharacterGraphTool error")
            return ToolResult.fail(f"character_graph 执行失败: {e}")
