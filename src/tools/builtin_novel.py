"""Novel search tool — Agent-facing tool for novel dialogue RAG.

Integrates IntentRouter + NovelRetrieval + ImpersonationService into
a single BaseTool that the Agent can invoke via function calling.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from src.application.novel.impersonation import ImpersonationService
from src.application.novel.intent_router import IntentRouter
from src.application.novel.retrieval import NovelRetrieval
from src.tools.novel_search_handlers import NovelSearchHandlersMixin
from src.tools.base import BaseTool, ToolResult

logger = logging.getLogger("agent")

# Lazy singleton (shared across tool calls)
_store = None


def _get_store():
    """Get or create the shared hybrid novel store singleton."""
    global _store
    if _store is None:
        from src.application.novel.factory import create_novel_store
        _store = create_novel_store()
    return _store


def inject_store(store) -> None:
    """Inject a shared store so NovelSearchTool reuses the same embedding."""
    global _store
    _store = store


class NovelSearchTool(NovelSearchHandlersMixin, BaseTool):
    """Search and interact with novel knowledge bases.

    Supports three core actions:
    - search: Query novels for plot details, character info, or original text.
    - impersonate: Generate in-character dialogue mimicking a novel persona.
    - import: Import a cleaned MD file into the knowledge base.
    """

    name: str = "novel_search"
    description: str = (
        "小说知识库与书目（优先于 web_search；禁止用 file_operation 读 data/novels）。\n"
        "action 说明：\n"
        "  - search：四通道语义检索（narrative/dialogue/qa/character）\n"
        "  - list：书目元数据（系列/卷/章节数/block_counts/needs_reindex）\n"
        "  - list_chapters：某卷完整章节目录（真实章名+序号；不要用 search 查「目录」）\n"
        "  - global：GraphRAG 全局问答（跨章节主线/整体关系；需已构建，未构建时提示）\n"
        "  - impersonate：单次角色模仿；import：从 uploads 导入\n"
        "查某卷：doc_id=「系列__vol03」或 query 写「第3卷」。\n"
        "查第N节/章：按目录顺序映射真实章名（书中通常没有「第五节」标题）。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["search", "impersonate", "import", "list", "list_chapters", "global"],
                "description": (
                    "search=检索；list=书目元数据；list_chapters=卷内章节目录；"
                    "global=GraphRAG 全局问答；impersonate=单次模仿；import=导入"
                ),
            },
            "query": {
                "type": "string",
                "description": (
                    "搜索/目录查询（如 '第3卷第5节'、'第3卷目录'、剧情问题）"
                    "或角色模仿需求、或 global 全局问题"
                ),
            },
            "channel": {
                "type": "string",
                "enum": ["narrative", "dialogue", "qa", "character"],
                "description": (
                    "仅 search：显式通道。"
                    "含「第N节/章」时默认 narrative。列目录请用 list_chapters。"
                ),
            },
            "character": {
                "type": "string",
                "description": "目标角色名，仅 impersonate 操作必填（如 '苏瑶'）",
            },
            "doc_id": {
                "type": "string",
                "description": (
                    "可选：锁定单卷，格式「系列名__vol01」。"
                    "list_chapters 强烈建议传入；也可在 query 写「第N卷」。"
                ),
            },
            "series": {
                "type": "string",
                "description": (
                    "可选：锁定整个系列（多卷），格式为系列名（如「败犬女主太多了」）。"
                    "global 必填；其余 action 作用：检索严格限定在该系列内，绝不跨作品。"
                    "优先级：doc_id > series > 全库；用户提到作品名/系列名时必传。"
                ),
            },
            "style": {
                "type": "string",
                "description": "期望的语气风格，仅 impersonate 操作（如 '清冷'、'温柔'）",
            },
            "top_k": {
                "type": "integer",
                "description": "返回结果数量（1-10，默认 5）",
                "default": 5,
            },
        },
        "required": ["action"],
    }

    _ALLOWED_IMPORT_SUFFIXES = {".md", ".txt", ".epub"}
    _MAX_IMPORT_BYTES = 20 * 1024 * 1024

    def __init__(
        self,
        store=None,
        import_dir: str | Path | None = None,
    ):
        self._store = store or _get_store()
        self._import_dir = Path(
            import_dir or os.getenv("NOVEL_IMPORT_DIR", "data/uploads")
        ).resolve()
        from src.application.novel.factory import create_novel_retrieval
        # Prefer factory retrieval (router weights / reranker / graph) when using shared store
        try:
            self._retrieval = create_novel_retrieval(store=self._store)
            self._router = self._retrieval.router
        except Exception:
            self._router = IntentRouter()
            self._retrieval = NovelRetrieval(self._store, router=self._router)
        llm = self._build_impersonation_llm()
        self._impersonator = ImpersonationService(self._store, router=self._router, llm_client=llm)

    @staticmethod
    def _build_impersonation_llm():
        """Build LLM client from config.yaml novel_rag impersonation settings."""
        try:
            from src.application.novel.factory import create_impersonation_service
            svc = create_impersonation_service()
            return svc.llm_client
        except Exception:
            return None

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Execute novel search or impersonation.

        Args:
            action: "search" | "impersonate" | "import"
            query: Search query or impersonation request.
            character: Character name (impersonate only).
            doc_id: Optional book filter.
            style: Style hint (impersonate only).
            top_k: Max results.

        Returns:
            ToolResult with formatted results.
        """
        try:
            self.validate_args(kwargs)
            action: str = kwargs["action"]

            if action == "search":
                return await self._handle_search(kwargs)
            elif action == "impersonate":
                return await self._handle_impersonate(kwargs)
            elif action == "import":
                return await self._handle_import(kwargs)
            elif action == "list":
                return await self._handle_list(kwargs)
            elif action == "list_chapters":
                return await self._handle_list_chapters(kwargs)
            elif action == "global":
                return await self._handle_global(kwargs)
            else:
                return ToolResult.fail(f"Unknown action: {action}")

        except ValueError as e:
            return ToolResult.fail(str(e))
        except Exception as e:
            # 读类工具：优雅降级为 fail，不打断工具循环（LLM 可重试/换工具）
            logger.exception("NovelSearchTool error")
            return ToolResult.fail(f"novel_search 执行失败: {e}")

    # ── Action handlers ─────────────────────────────────

