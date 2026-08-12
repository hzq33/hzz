"""GraphRAG 全局问答工具 — 跨章节主线 / 整体关系网问答（读）。

数据来自 GraphRAG 全局层（社区发现 + LLM 社区摘要），与前端「世界体系 →
全局问答」tab 同源。构建由 story_analysis build 联动或 API /rag-global/build 触发。
"""

from __future__ import annotations

import logging
from typing import Any

from src.tools.base import BaseTool, ToolResult

logger = logging.getLogger("agent")


class GraphRagTool(BaseTool):
    """读取 GraphRAG 全局层：社区摘要、全局概览、全局问答上下文。"""

    name: str = "graph_rag"
    description: str = (
        "GraphRAG 全局问答：跨章节的主线/整体关系网（社区摘要 + 全局概览）。\n"
        "当问题涉及「整个故事的主线」「整体关系」「跨越多个章节的脉络」「全局视角」时调用；\n"
        "单点情节/角色细节请用 novel_search search 或 world_knowledge。\n"
        "需要 series_id（系列名）。未构建时返回提示，可先跑 story_analysis build。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["overview", "ask", "status"],
                "description": (
                    "overview=读取全局概览+社区列表；ask=全局问答（带 query）；"
                    "status=仅查构建状态/时间"
                ),
                "default": "overview",
            },
            "series_id": {
                "type": "string",
                "description": "系列 ID（如「败犬女主太多了」）",
            },
            "query": {
                "type": "string",
                "description": "全局问答问题（action=ask 时使用）",
            },
        },
        "required": ["series_id"],
    }

    async def execute(self, **kwargs: Any) -> ToolResult:
        try:
            self.validate_args(kwargs)
            series_id = (kwargs.get("series_id") or "").strip()
            if not series_id:
                return ToolResult.fail("graph_rag 需要 series_id（系列名）")
            action = (kwargs.get("action") or "overview").strip()
            query = (kwargs.get("query") or "").strip()
            if action not in ("overview", "ask", "status"):
                return ToolResult.fail(f"Unknown action: {action}")

            try:
                from src.application.novel.services.graph_rag_service import (
                    format_global_context,
                    is_stale,
                    load_graph_rag,
                )
            except Exception as exc:  # noqa: BLE001
                return ToolResult.fail(f"GraphRAG service unavailable: {exc}")

            payload = load_graph_rag(series_id)
            if payload is None:
                return ToolResult.ok(
                    f"系列「{series_id}」尚未构建 GraphRAG。"
                    "请先运行 story_analysis build（会联动构建），"
                    "或调用 API POST /api/v1/agent/rag-global/build。"
                )

            stale = is_stale(series_id)
            updated = payload.get("updated_at", "")
            communities = payload.get("communities") or []

            if action == "status":
                return ToolResult.ok(
                    f"series={series_id} exists=true stale={stale} "
                    f"updated_at={updated} communities={len(communities)}"
                )

            lines = [
                f"## GraphRAG 全局层 — {series_id}",
                f"stale={stale} updated_at={updated} 社区数={len(communities)}",
                "",
            ]
            if action == "ask" and query:
                try:
                    ctx = format_global_context(series_id, query)
                except Exception as exc:  # noqa: BLE001
                    ctx = f"（全局问答失败: {exc}）"
                lines.append(f"## 全局问答（问题: {query}）")
                lines.append(ctx or "（无匹配社区摘要）")
            else:
                overview = payload.get("global_overview") or ""
                lines.append(f"全局概览: {overview}")
                lines.append("")
                lines.append("## 社区列表")
                if not communities:
                    lines.append("（无社区摘要）")
                for i, c in enumerate(communities[:20], 1):
                    label = c.get("label") or c.get("summary") or ""
                    lines.append(f"{i}. {str(label)[:150]}")
                if len(communities) > 20:
                    lines.append(f"…另有 {len(communities) - 20} 个社区未列出")
            return ToolResult.ok("\n".join(lines))
        except ValueError as e:
            return ToolResult.fail(str(e))
        except Exception as e:
            logger.exception("GraphRagTool error")
            return ToolResult.fail(f"graph_rag 执行失败: {e}")
