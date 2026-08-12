"""Narrative Parent/Child expand — application 层入口（薄转发）。

core（代理编排层）不直连 domain：本模块转发 domain 的纯函数
expand_narrative_hits，与 qa_expand / character_channel_index 同层，
保证 core → application → domain 的分层链路。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def expand_narrative_hits(
    store: Any,
    hits: Sequence[Any],
    *,
    radius: int = 1,
    max_expanded_chars: int = 3500,
    chapter_hard_boundary: bool = True,
) -> list[Any]:
    """把 Child/小块命中展开为同章 Parent 邻域（按 Parent 去重）。"""
    from src.domain.novel.narrative_expand import expand_narrative_hits as _expand

    return _expand(
        store,
        hits,
        radius=radius,
        max_expanded_chars=max_expanded_chars,
        chapter_hard_boundary=chapter_hard_boundary,
    )
