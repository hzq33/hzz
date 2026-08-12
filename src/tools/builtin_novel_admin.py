"""Novel admin tool — rename / delete / reindex flags (HITL for writes)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from src.tools.base import BaseTool, ToolResult
from src.utils.errors import ToolExecutionError

logger = logging.getLogger("agent")


class NovelAdminTool(BaseTool):
    """Write-side novel catalog and volume management."""

    name: str = "novel_admin"
    description: str = (
        "小说书目/对话管理（写操作，风险高）：重命名系列、删卷、清 sidecar、"
        "对话重抽取（redialogue）。删除/重命名/清理/重抽为不可逆写操作，需人工审批。\n"
        "读目录/检索请用 novel_search（list / list_chapters / search）。\n"
        "delete_volume / rename_series / purge_series / redialogue 需人工审批。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "rename_series",
                    "delete_volume",
                    "purge_series",
                    "reindex_flags",
                    "redialogue",
                ],
                "description": (
                    "rename_series=改展示名（不可逆）；delete_volume=删卷（不可逆，"
                    "同时删向量库与 catalog）；purge_series=清系列全部 sidecar "
                    "（不可逆）；reindex_flags=查看 needs_reindex（只读）；"
                    "redialogue=重跑某卷对话提取（write_back=true 会替换 dialogue 块，"
                    "耗 LLM，需审批）"
                ),
            },
            "series_id": {"type": "string", "description": "系列 ID"},
            "series_title": {
                "type": "string",
                "description": "新的系列展示名（rename_series）",
            },
            "doc_id": {
                "type": "string",
                "description": "卷 doc_id（delete_volume / redialogue）",
            },
            "write_back": {
                "type": "boolean",
                "description": "redialogue：是否替换该卷 dialogue 块（默认 false=仅分析不入库）",
                "default": False,
            },
            "sample_n": {
                "type": "integer",
                "description": "redialogue：抽样章节数（0=全部，最大 500）",
                "default": 0,
            },
        },
        "required": ["action"],
    }

    def __init__(self, store=None):
        self._store = store

    def _get_store(self):
        if self._store is not None:
            return self._store
        from src.application.novel.factory import create_novel_store

        self._store = create_novel_store()
        return self._store

    def inject_store(self, store) -> None:
        """注入共享 store（上传新书后由 server 统一广播，保证索引一致）。"""
        self._store = store

    async def execute(self, **kwargs: Any) -> ToolResult:
        try:
            self.validate_args(kwargs)
            action = kwargs["action"]
            if action == "rename_series":
                return self._rename(kwargs)
            if action == "delete_volume":
                return await self._delete_volume(kwargs)
            if action == "purge_series":
                return self._purge_series(kwargs)
            if action == "reindex_flags":
                return self._reindex_flags(kwargs)
            if action == "redialogue":
                return await self._redialogue(kwargs)
            return ToolResult.fail(f"Unknown action: {action}")
        except ValueError as e:
            return ToolResult.fail(str(e))
        except Exception as e:
            logger.exception("NovelAdminTool error")
            raise ToolExecutionError(str(e), tool_name=self.name) from e

    def _rename(self, kwargs: dict) -> ToolResult:
        sid = (kwargs.get("series_id") or "").strip()
        title = (kwargs.get("series_title") or "").strip()
        if not sid or not title:
            return ToolResult.fail("rename_series 需要 series_id 与 series_title")
        from src.application.novel.services.catalog_service import rename_series

        catalog = rename_series(sid, title)
        if not catalog:
            return ToolResult.fail(f"系列不存在：{sid}")
        return ToolResult.ok(
            f"已重命名：series_id={catalog.series_id} → title={catalog.series_title}"
        )

    async def _delete_volume(self, kwargs: dict) -> ToolResult:
        doc_id = (kwargs.get("doc_id") or "").strip()
        if not doc_id:
            return ToolResult.fail("delete_volume 需要 doc_id")
        from src.application.novel.services.catalog_service import (
            delete_volume_from_catalog,
            list_catalogs,
            load_catalog,
            purge_series_artifacts,
        )

        series_id = (kwargs.get("series_id") or "").strip()
        if not series_id:
            for catalog in list_catalogs():
                if any(v.doc_id == doc_id for v in catalog.volumes):
                    series_id = catalog.series_id
                    break
        store = self._get_store()
        try:
            await store.delete_by_doc_id(doc_id)
        except Exception as exc:
            logger.warning("Vector delete for %s: %s", doc_id, exc)

        catalog_after = None
        if series_id:
            catalog_after = delete_volume_from_catalog(series_id, doc_id)
        remaining = bool(catalog_after and catalog_after.volumes)
        if series_id and not remaining and not load_catalog(series_id):
            try:
                purge_series_artifacts(series_id)
            except Exception:
                pass
        return ToolResult.ok(
            f"已删除卷 doc_id={doc_id}"
            + (f"（series={series_id}）" if series_id else "")
        )

    def _purge_series(self, kwargs: dict) -> ToolResult:
        sid = (kwargs.get("series_id") or "").strip()
        if not sid:
            return ToolResult.fail("purge_series 需要 series_id")
        from src.application.novel.services.catalog_service import purge_series_artifacts

        stats = purge_series_artifacts(sid)
        return ToolResult.ok(f"已清理系列 sidecar：{sid} stats={stats}")

    def _reindex_flags(self, kwargs: dict) -> ToolResult:
        from src.application.novel.services.catalog_service import ensure_series_title, list_catalogs

        sid = (kwargs.get("series_id") or "").strip()
        lines = ["## needs_reindex 标志", ""]
        found = False
        for catalog in list_catalogs():
            ensure_series_title(catalog)
            if sid and catalog.series_id != sid:
                continue
            for vol in catalog.volumes:
                if not vol.needs_reindex:
                    continue
                found = True
                lines.append(
                    f"- {vol.doc_id}: needs_reindex=true"
                    + (f"（{vol.reindex_reason}）" if vol.reindex_reason else "")
                )
        if not found:
            return ToolResult.ok("无卷标记 needs_reindex。")
        return ToolResult.ok("\n".join(lines))

    async def _redialogue(self, kwargs: dict) -> ToolResult:
        """重跑某卷的对话提取/归因（耗 LLM，写操作需审批）。"""
        doc_id = (kwargs.get("doc_id") or "").strip()
        if not doc_id:
            return ToolResult.fail("redialogue 需要 doc_id（格式「系列__vol01」）")
        write_back = bool(kwargs.get("write_back"))
        sample_n = int(kwargs.get("sample_n") or 0)

        try:
            from src.application.novel.redialogue import (
                DocNotFoundError,
                InventoryMissingError,
                load_series_inventory,
                run_redialogue,
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult.fail(f"redialogue service unavailable: {exc}")

        series_id = doc_id.split("__", 1)[0] if "__" in doc_id else doc_id
        seed, chars = load_series_inventory(series_id)
        if not seed and not chars:
            return ToolResult.fail(
                f"系列「{series_id}」无 inventory。"
                f"请先运行: python scripts/dev/rebuild_inventory.py {series_id}"
            )

        llm = None
        try:
            from src.application.novel.factory import create_impersonation_service

            svc = create_impersonation_service()
            llm = getattr(svc, "llm_client", None) or getattr(svc, "llm", None)
        except Exception:  # noqa: BLE001 - LLM 不可用时由 run_redialogue 降级
            llm = None

        try:
            result = await run_redialogue(
                doc_id,
                write_back=write_back,
                sample_n=sample_n,
                llm_client=llm,
            )
        except (InventoryMissingError, DocNotFoundError) as exc:
            return ToolResult.fail(str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.exception("redialogue failed")
            return ToolResult.fail(f"redialogue 执行失败: {exc}")

        lines = [
            f"## 对话重抽取完成 — {doc_id}",
            f"章节数={result.chapters} 块数={result.blocks} 轮次={result.turns}",
            f"LLM 调用={result.llm_calls} 新增块={result.new_blocks}"
            + (f" 删除块={result.deleted_blocks}" if result.deleted_blocks else ""),
            f"写回={result.written_back}"
            + (f" 结果文件={Path(result.result_path).name}" if result.result_path else ""),
        ]
        if result.meta:
            lines.append(f"meta={result.meta}")
        return ToolResult.ok("\n".join(lines))
