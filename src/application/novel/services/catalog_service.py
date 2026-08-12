"""CatalogService — 小说目录（catalog）管理管线的应用层入口（薄封装）。

统一暴露 catalog 读写（load/list/rename/delete/orphan/ensure_title）
与系列清理编排（purge_series_artifacts），调用方不再直连 domain 的
catalog / series_cleanup 模块。
"""

from __future__ import annotations

from typing import Any


def load_catalog(series_id: str) -> Any:
    """读取系列 catalog（NovelCatalog | None）。"""
    from src.domain.novel.catalog import load_catalog as _load

    return _load(series_id)


def list_catalogs() -> list[Any]:
    """列出全部系列 catalog。"""
    from src.domain.novel.catalog import list_catalogs as _list

    return _list()


def ensure_series_title(catalog: Any) -> Any:
    """仅当 series_title 为空时填充（绝不覆盖真实系列名）。"""
    from src.domain.novel.catalog import ensure_series_title as _ensure

    return _ensure(catalog)


def find_orphan_doc_ids(store_doc_ids: list[str]) -> list[str]:
    """找出 lance 有数据但未被任何 catalog 收录的卷（孤儿卷）。"""
    from src.domain.novel.catalog import find_orphan_doc_ids as _find

    return _find(store_doc_ids)


def delete_volume_from_catalog(series_id: str, doc_id: str) -> Any:
    """从 catalog 删除一卷（NovelCatalog | None）。"""
    from src.domain.novel.catalog import delete_volume_from_catalog as _delete

    return _delete(series_id, doc_id)


def rename_series(series_id: str, series_title: str) -> Any:
    """更新系列显示名（series_id 保持不变）。"""
    from src.domain.novel.catalog import rename_series as _rename

    return _rename(series_id, series_title)


def purge_series_artifacts(series_id: str) -> dict[str, Any]:
    """删除系列全部 sidecar（roster/inventory/cards/analysis）。

    末卷删除时调用，避免 Knowledge UI 与 Alias Roster Monitor 显示幽灵数据。
    """
    from src.domain.novel.series_cleanup import purge_series_artifacts as _purge

    return _purge(series_id)
