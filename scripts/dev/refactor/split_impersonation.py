"""impersonation_agent.py mixin 拆分脚本 — 按方法行号提取到 _core/_retrieval mixin。

用法: python scripts/dev/refactor/split_impersonation.py
"""
from pathlib import Path

SRC = Path("src/core/impersonation_agent.py")
PKG = Path("src/core")
lines = SRC.read_text(encoding="utf-8").splitlines(keepends=True)


def get(start: int, end: int) -> str:
    """1-based inclusive [start, end]."""
    return "".join(lines[start - 1 : end])


def extract_methods(specs: list[tuple[str, int, int]]) -> str:
    """提取方法体，缩进保持（方法原为 4 空格缩进，mixin 类内同样是 4 空格）。"""
    parts = []
    for name, start, end in specs:
        body = get(start, end)
        # 去掉尾部多余空行，保留 2 个
        body = body.rstrip("\n") + "\n\n"
        parts.append(body)
    return "".join(parts)


# 方法边界（下一个方法起始行 - 1）
core_methods = [
    ("_setup_system_prompt", 215, 217),
    ("_append_citations", 219, 230),
    ("_citations_event_payload", 232, 241),
    ("chat", 245, 258),
    ("chat_stream", 260, 264),
    ("iter_chat_events", 266, 302),
    ("pop_last_assistant", 304, 322),
    ("pop_last_user", 324, 339),
    ("reset", 341, 347),
    ("get_history", 349, 350),
    ("_chat_with_tools", 354, 357),
    ("_prepare_final_messages", 359, 424),
    ("_build_messages", 426, 459),
    ("_build_api_messages", 461, 466),
]

retrieval_methods = [
    ("_style_name_set", 470, 477),
    ("_speaker_matches_style", 479, 486),
    ("_norm_style_text", 488, 490),   # 含 @staticmethod(488)
    ("_card_sample_norms", 492, 499),
    ("_style_search_query", 501, 514),
    ("_extract_character_style_turns", 516, 554),
    ("_style_hit_mentions_character", 556, 570),
    ("_filter_style_hits", 572, 584),
    ("_retrieve_style_samples", 586, 653),
    ("_retrieve_style_samples_legacy", 655, 682),
    ("_retrieve_fact_context", 684, 741),
    ("_retrieve_relation_event_context", 743, 849),
    ("_retrieve_narrative_context", 851, 912),
]

core_head = '''"""ImpersonationAgent chat core mixin — conversation loop, tool loop, messages.

Extracted from the former monolithic ``impersonation_agent.py``; logic unchanged.
Mixin methods share instance state (``self._card`` / ``self._store`` / ``self._llm``).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncGenerator

logger = logging.getLogger("agent")


class ImpersonationChatMixin:
    """Chat / tool-loop / message-building methods."""

    # Class constants referenced by mixin methods (defined here; subclass sees them).
    _STYLE_SAMPLE_TURNS: int = 3
    _STYLE_TOP_K: int = 3
    _STYLE_FETCH_K: int = 8
    _STYLE_MODE: str = "pool_turn"
    _STYLE_SKIP_ON_FACT_QUESTION: bool = True
    _STYLE_MIN_SCORE: float = 0.45
    _STYLE_REQUIRE_CHARACTER_MENTION: bool = True
    _NARRATIVE_TOP_K: int = 3
    _MAX_TOOL_ROUNDS: int = 3
    _FACT_GROUNDING_HINT = (
        "设定冲突时以「原著参考」为准；参考未写明的细节（外貌、关系、经历等）"
        "请明确表示不确定，禁止编造。"
    )
    _NO_FACT_HINT = (
        "## 注意\\n"
        "本次未检索到可靠原著事实片段。涉及设定/外貌/关系时请明确表示不确定，"
        "禁止编造原文未写明的细节。"
    )


'''

retrieval_head = '''"""ImpersonationAgent retrieval mixin — RAG context retrieval methods.

Extracted from the former monolithic ``impersonation_agent.py``; logic unchanged.
Mixin methods share instance state (``self._store`` / ``self._card`` / ``self.character``).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("agent")


class ImpersonationRetrievalMixin:
    """Style / fact / relation-event / narrative retrieval methods."""


'''

Path(PKG / "impersonation" / "chat.py").write_text(core_head + extract_methods(core_methods), encoding="utf-8")
print("wrote impersonation/chat.py")
Path(PKG / "impersonation" / "retrieval.py").write_text(retrieval_head + extract_methods(retrieval_methods), encoding="utf-8")
print("wrote impersonation/retrieval.py")
