"""内置文件操作工具（读取 / 写入 / 列表）。"""

import logging
import os
from pathlib import Path
from typing import Any

from src.tools.base import BaseTool, ToolResult

logger = logging.getLogger("agent")


class FileOperationTool(BaseTool):
    """在受限工作目录内执行文件操作。

    支持读取、写入和列表操作。
    """

    name: str = "file_operation"
    description: str = (
        "在受限工作目录内读写/列出文件。"
        "不要用本工具查找小说原文或 data/novels（小说只在 novel_search 向量库中）。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["read", "write", "list"],
                "description": "要执行的操作：read（读取）、write（写入）或 list（列表）。",
            },
            "path": {
                "type": "string",
                "description": "文件或目录路径（相对于工作目录）。",
            },
            "content": {
                "type": "string",
                "description": "要写入的内容（仅 'write' 操作需要）。",
            },
        },
        "required": ["operation", "path"],
    }

    def __init__(self, working_dir: str = "data/workspace") -> None:
        """初始化，可选设置工作目录限制。

        Args:
            working_dir: 所有文件操作的基础目录。默认为当前目录。
        """
        self._working_dir = str(Path(working_dir).resolve())
        logger.info("FileOperationTool working directory: %s", self._working_dir)

    def _resolve_path(self, path: str) -> str:
        """解析并验证工作目录内的相对路径。

        Args:
            path: 用户提供的相对路径。

        Returns:
            在工作目录内解析后的绝对路径。

        Raises:
            ValueError: 如果路径试图逃离工作目录。
        """
        base = Path(self._working_dir).resolve()
        full_path = (base / path).resolve()
        try:
            full_path.relative_to(base)
        except ValueError:
            raise ValueError(
                f"访问被拒绝：路径 '{path}' 试图逃离工作目录。"
            )
        return str(full_path)

    async def execute(self, **kwargs: Any) -> ToolResult:
        """执行文件操作。

        Args:
            operation: 'read'、'write' 或 'list' 之一。
            path: 相对文件或目录路径。
            content: 要写入的文本内容（'write' 操作必需）。

        Returns:
            包含操作输出或错误的 ToolResult。
        """
        try:
            self.validate_args(kwargs)
            operation: str = kwargs["operation"]
            path: str = kwargs["path"]

            logger.info("File operation: %s '%s'", operation, path)

            full_path = self._resolve_path(path)

            if operation == "read":
                return await self._read_file(full_path)
            elif operation == "write":
                content: str = kwargs.get("content", "")
                return await self._write_file(full_path, content)
            elif operation == "list":
                return await self._list_dir(full_path)
            else:
                return ToolResult.fail(f"Unknown operation: {operation}")

        except ValueError as e:
            logger.error("File operation validation error: %s", e)
            return ToolResult.fail(str(e))
        except Exception as e:
            logger.error("File operation unexpected error: %s", e)
            from src.utils.errors import ToolExecutionError

            raise ToolExecutionError(
                f"File operation failed: {e}",
                tool_name=self.name,
                original_error=str(e),
            ) from e

    async def _read_file(self, path: str) -> ToolResult:
        """从磁盘读取文件。"""
        if not os.path.isfile(path):
            return ToolResult.fail(f"File not found: {path}")
        try:
            with open(path, encoding="utf-8") as f:
                content = f.read()
            return ToolResult.ok(content)
        except UnicodeDecodeError:
            return ToolResult.fail(f"Cannot read file as UTF-8 text: {path}")
        except OSError as e:
            return ToolResult.fail(f"Error reading file: {e}")

    async def _write_file(self, path: str, content: str) -> ToolResult:
        """将内容写入文件。"""
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return ToolResult.ok(f"Successfully wrote {len(content)} bytes to {path}")
        except OSError as e:
            return ToolResult.fail(f"Error writing file: {e}")

    async def _list_dir(self, path: str) -> ToolResult:
        """列出目录内容。"""
        if not os.path.isdir(path):
            return ToolResult.fail(f"Directory not found: {path}")
        try:
            entries = os.listdir(path)
            if not entries:
                return ToolResult.ok("(empty directory)")
            lines = [f"Contents of {path}:"]
            for entry in sorted(entries):
                entry_path = os.path.join(path, entry)
                prefix = "[DIR] " if os.path.isdir(entry_path) else "[FILE]"
                lines.append(f"  {prefix} {entry}")
            return ToolResult.ok("\n".join(lines))
        except OSError as e:
            return ToolResult.fail(f"Error listing directory: {e}")
