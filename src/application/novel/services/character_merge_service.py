"""CharacterMergeService — 角色合并管线的应用层入口（薄封装）。"""

from __future__ import annotations

from typing import Any


def suggest_merges(series_id: str, *, min_score: float = 0.92) -> list[Any]:
    """给出近重复角色合并建议。"""
    from src.domain.novel.character_merge import suggest_character_merges

    return suggest_character_merges(series_id, min_score=min_score)


def merge(series_id: str, survivor: str, merge_names: list[str]) -> Any:
    """合并近重复角色到 survivor（ValueError 上抛，由调用方转 4xx）。"""
    from src.domain.novel.character_merge import merge_characters

    return merge_characters(
        series_id=series_id,
        survivor=survivor,
        merge_names=merge_names,
    )
