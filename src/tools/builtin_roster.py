"""角色盘点（别名名录）工具 — 读取系列角色规范名 + 别名（读）。

数据来自 alias.json（规范化角色名 + 观察到的别名），与前端「世界体系 →
角色盘点」tab 同源。仅读；写（改别名/规范名）请走 API PUT /roster。
"""

from __future__ import annotations

import logging
from typing import Any

from src.tools.base import BaseTool, ToolResult

logger = logging.getLogger("agent")


class RosterTool(BaseTool):
    """读取系列角色盘点（规范名 + 别名）。"""

    name: str = "roster"
    description: str = (
        "角色盘点/别名名录：系列的规范角色名 + 别名（角色有哪些称呼、歧义消解）。\n"
        "当问题涉及「某角色都有哪些名字/别名」「某系列有哪些角色」时调用；\n"
        "需要 series_id；list 可列所有已有盘点的系列。仅读操作。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["get", "list"],
                "description": (
                    "get=读取某系列盘点（需要 series_id）；list=列出已有盘点系列"
                ),
                "default": "get",
            },
            "series_id": {
                "type": "string",
                "description": "系列 ID（action=get 必填）",
            },
            "name": {
                "type": "string",
                "description": "可选：只返回该角色及其别名（get）",
            },
        },
        "required": ["action"],
    }

    async def execute(self, **kwargs: Any) -> ToolResult:
        try:
            action = (kwargs.get("action") or "get").strip()
            if action not in ("get", "list"):
                return ToolResult.fail(f"Unknown action: {action}")

            try:
                from src.api.routers.alias_roster import list_series, read_alias
            except Exception as exc:  # noqa: BLE001
                return ToolResult.fail(f"Roster service unavailable: {exc}")

            if action == "list":
                ids = list_series()
                if not ids:
                    return ToolResult.ok("尚无任何角色盘点（alias.json）。")
                from src.application.novel.services.catalog_service import load_catalog

                lines = ["## 已有角色盘点系列", ""]
                for sid in ids:
                    catalog = load_catalog(sid)
                    title = (catalog.series_title or catalog.series_id) if catalog else sid
                    lines.append(f"- {title}（series_id={sid}）")
                return ToolResult.ok("\n".join(lines))

            series_id = (kwargs.get("series_id") or "").strip()
            if not series_id:
                return ToolResult.fail("get 需要 series_id（系列名）")
            data = read_alias(series_id)
            if data.get("meta", {}).get("error"):
                return ToolResult.ok(
                    f"系列「{series_id}」无角色盘点（alias.json）。"
                    "可先跑角色盘点管线（character_inventory）。"
                )
            entities = data.get("entities") or []
            name_filter = (kwargs.get("name") or "").strip()

            lines = [
                f"## 角色盘点 — {series_id}",
                f"实体数={len(entities)} updated_at={data.get('updated_at', '')}",
                "",
            ]
            shown = 0
            for ent in entities:
                canonical = (
                    ent.get("canonical_name")
                    or ent.get("canonical")
                    or ent.get("name")
                    or "?"
                )
                aliases = (
                    ent.get("aliases_observed")
                    or ent.get("aliases")
                    or []
                )
                importance = ent.get("importance") or ""
                mentions = ent.get("mention_count")
                if name_filter and name_filter not in canonical and not any(
                    name_filter in str(a) for a in aliases
                ):
                    continue
                shown += 1
                line = f"- {canonical}"
                if importance:
                    line += f" [{importance}]"
                if mentions is not None:
                    line += f"（提及{mentions}次）"
                if aliases:
                    line += f"｜别名: {', '.join(str(a) for a in aliases[:10])}"
                    if len(aliases) > 10:
                        line += f"…共{len(aliases)}"
                lines.append(line)
                if shown >= 100:
                    lines.append("…超出 100 条截断")
                    break
            if shown == 0:
                lines.append("（无匹配实体）")
            lines.append("")
            lines.append("提示：改别名/规范名请用 API PUT /api/v1/agent/characters/roster。")
            return ToolResult.ok("\n".join(lines))
        except ValueError as e:
            return ToolResult.fail(str(e))
        except Exception as e:
            logger.exception("RosterTool error")
            return ToolResult.fail(f"roster 执行失败: {e}")
