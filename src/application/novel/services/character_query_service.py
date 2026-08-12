"""CharacterQueryService — 角色数据查询管线的应用层入口（薄封装）。

统一暴露 roster / inventory / alias 的读取与保存，调用方不再直连
domain 的 character_roster / character_inventory / alias_map / alias_sync。
"""

from __future__ import annotations

from typing import Any


def load_roster(series_id: str) -> Any:
    """读取角色名录（CharacterRoster | None）。"""
    from src.domain.novel.character_roster import load_roster as _load

    return _load(series_id)


def save_roster(roster: Any) -> Any:
    """持久化角色名录（返回 Path）。"""
    from src.domain.novel.character_roster import save_roster as _save

    return _save(roster)


def load_inventory_candidates(series_id: str) -> dict[str, Any] | None:
    """读取角色盘点候选（inventory json | None）。"""
    from src.domain.novel.character_inventory import load_inventory_candidates as _load

    return _load(series_id)


def load_alias_map(series_id: str) -> Any:
    """读取别名映射表（AliasMap | None）。"""
    from src.domain.novel.alias_map import load_alias_map as _load

    return _load(series_id)


def save_alias_map(amap: Any) -> Any:
    """持久化别名映射表（返回 Path）。"""
    from src.domain.novel.alias_map import save_alias_map as _save

    return _save(amap)


def sync_alias_roster_save(
    series_id: str,
    old_data: dict[str, Any] | None,
    new_data: dict[str, Any],
) -> list[dict[str, Any]]:
    """别名名录保存后：传播 canonical 改名到卡/名录（返回重命名列表）。"""
    from src.domain.novel.alias_sync import sync_alias_roster_save as _sync

    return _sync(series_id, old_data, new_data)
