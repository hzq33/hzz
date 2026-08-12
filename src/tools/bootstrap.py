"""Tool bootstrap — single entry point for registering built-in tools.

Eliminates duplicate _register_builtin_tools() in main.py and agent_server.py.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.tools.registry import ToolRegistry
    from src.utils.config import AgentConfig

logger = logging.getLogger("agent")

_BUILTIN_TOOL_FACTORIES: dict[str, str] = {
    "web_search": "src.tools.builtin_search:WebSearchTool",
    "execute_code": "src.tools.builtin_code:ExecuteCodeTool",
    "file_operation": "src.tools.builtin_file:FileOperationTool",
    "novel_search": "src.tools.builtin_novel:NovelSearchTool",
    "world_knowledge": "src.tools.builtin_world_knowledge:WorldKnowledgeTool",
    "character_kb": "src.tools.builtin_character:CharacterKbTool",
    "graph_rag": "src.tools.builtin_graph_rag:GraphRagTool",
    "character_graph": "src.tools.builtin_character_graph:CharacterGraphTool",
    "roster": "src.tools.builtin_roster:RosterTool",
    "story_analysis": "src.tools.builtin_story:StoryAnalysisTool",
    "novel_admin": "src.tools.builtin_novel_admin:NovelAdminTool",
}


def register_builtin_tools(
    registry: ToolRegistry,
    config: AgentConfig,
    working_dir: str | None = None,
) -> None:
    """Register all built-in tools specified in config.tools.builtin.

    This is the single source of truth for tool registration. Both CLI (main.py)
    and HTTP server (agent_server.py) should call this function.

    Args:
        registry: The ToolRegistry instance.
        config: The validated AgentConfig.
        working_dir: Base directory for file operations. If None, uses CWD.
    """
    builtin_names = config.tools.builtin
    if not builtin_names:
        return

    for tool_name in builtin_names:
        if tool_name == "execute_code" and os.getenv(
            "EXECUTE_CODE_ENABLED", ""
        ).lower() not in {"1", "true", "yes"}:
            logger.warning(
                "Skipping disabled tool 'execute_code'; set "
                "EXECUTE_CODE_ENABLED=true to opt in"
            )
            continue
        try:
            _register_one(registry, tool_name, working_dir)
        except Exception as e:
            logger.warning("Failed to register tool '%s': %s", tool_name, e)


def _register_one(
    registry: ToolRegistry,
    tool_name: str,
    working_dir: str | None = None,
) -> None:
    """Register a single built-in tool by name.

    Args:
        registry: The ToolRegistry instance.
        tool_name: Built-in tool name (key in _BUILTIN_TOOL_FACTORIES).
        working_dir: Base directory for file operations (file_operation only).
    """
    spec = _BUILTIN_TOOL_FACTORIES.get(tool_name)
    if not spec:
        logger.warning("Unknown built-in tool: %s", tool_name)
        return

    module_path, class_name = spec.split(":", 1)

    # Lazy import
    import importlib
    module = importlib.import_module(module_path)
    tool_cls = getattr(module, class_name)

    if tool_name == "file_operation":
        base_dir = working_dir or os.getenv("AGENT_WORKSPACE_DIR", "data/workspace")
        Path(base_dir).mkdir(parents=True, exist_ok=True)
        tool_instance = tool_cls(working_dir=base_dir)
    elif tool_name == "novel_search":
        import_dir = os.getenv("NOVEL_IMPORT_DIR", "data/uploads")
        Path(import_dir).mkdir(parents=True, exist_ok=True)
        tool_instance = tool_cls(import_dir=import_dir)
    else:
        tool_instance = tool_cls()

    registry.register(tool_instance)
    # 持 store 的工具注册到广播表：上传新书后统一刷新同一实例
    # （novel_search 用模块级单例，由 server 直接调 builtin_novel.inject_store）
    if tool_name in ("character_kb", "story_analysis", "novel_admin"):
        try:
            from src.application.tool_store_broadcast import register_store_holder

            register_store_holder(tool_instance)
        except Exception:  # noqa: BLE001
            pass
    logger.debug("Registered tool: %s", tool_name)
