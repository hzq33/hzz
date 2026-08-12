"""builtin_novel.py mixin 拆分脚本 — 提取 handler 方法到 _novel_search_handlers。

用法: python scripts/dev/refactor/split_builtin_novel.py
"""
from pathlib import Path

SRC = Path("src/tools/builtin_novel.py")
PKG = Path("src/tools")
lines = SRC.read_text(encoding="utf-8").splitlines(keepends=True)


def get(start: int, end: int) -> str:
    return "".join(lines[start - 1 : end])


# handler 方法（191-723）：_resolve_search_scope(191-250), _handle_search(251-341),
# _format_channel_results(342-426), _handle_impersonate(427-465), _handle_import(466-523),
# _resolve_import_path(524-548), _resolve_doc_id(549-567), _handle_list_chapters(568-648),
# _handle_list(649-723)
handlers = get(191, 723)

head = '''"""NovelSearchTool handlers mixin — search / impersonate / import / list actions.

Extracted from the former monolithic ``builtin_novel.py``; logic unchanged.
Mixin methods share instance state (``self._store`` / ``self.llm`` / ``self.name``).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("agent")


class NovelSearchHandlersMixin:
    """Action handlers for NovelSearchTool."""


'''

(PKG / "novel_search_handlers.py").write_text(head + handlers, encoding="utf-8")
print(f"wrote novel_search_handlers.py ({len((head+handlers).splitlines())} lines)")

import re
methods = re.findall(r"^    (?:async )?def (\w+)", handlers, re.MULTILINE)
print("mixin 方法:", methods)
