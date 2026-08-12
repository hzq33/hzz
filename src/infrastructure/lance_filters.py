"""Lance where-clause helpers (no lancedb import — CI-safe)."""

from __future__ import annotations

from typing import Any


def sql_escape(value: str) -> str:
    """Escape single quotes for Lance ``where`` string literals."""
    return str(value).replace("'", "''")


def metadata_prefilter_clauses(filters: dict[str, Any] | None) -> list[str]:
    """Build Lance prefilter clauses for chapter / character / doc_prefix metadata."""
    if not filters:
        return []
    parts: list[str] = []
    chapter = filters.get("chapter") or filters.get("chapter_title")
    if chapter:
        parts.append(f"chapter_title = '{sql_escape(str(chapter))}'")
    contains_any = filters.get("chapter_contains_any") or filters.get("chapter_contains")
    if contains_any:
        keys = (
            [contains_any]
            if isinstance(contains_any, str)
            else [str(k) for k in contains_any if k]
        )
        likes = [
            f"chapter_title LIKE '%{sql_escape(k)}%'"
            for k in keys[:8]
        ]
        if likes:
            parts.append("(" + " OR ".join(likes) + ")")
    chars = filters.get("characters") or filters.get("character")
    if chars:
        if isinstance(chars, str):
            names = [chars]
        else:
            names = [str(c) for c in chars if c]
        # characters_json is a JSON array string; LIKE is a recall prefilter.
        # 多角色名之间用 OR（块含任一角色即召回）——AND 语义要求块同时含
        # 所有角色名，多别名过滤时几乎必然零召回（与 _block_matches_filters
        # 的 isdisjoint/OR 语义保持一致）。
        likes = [
            f"characters_json LIKE '%\"{sql_escape(name)}\"%'"
            for name in names[:3]
        ]
        if len(likes) == 1:
            parts.append(likes[0])
        elif likes:
            parts.append("(" + " OR ".join(likes) + ")")
    # doc_prefix：系列级前过滤，将 doc_id LIKE 'prefix%' 转为 LanceDB 前过滤
    # 避免评估时全库扫描。用于 eval run_channel_search 的 doc_prefix 参数。
    doc_prefix = filters.get("doc_prefix")
    if doc_prefix:
        parts.append(f"doc_id LIKE '{sql_escape(str(doc_prefix))}%'")
    # series：系列级隔离（doc_id 命名规则为 {series_id}__vol{NN}）。
    # 锁定整个系列的全部卷，防止跨作品检索污染。
    # 注意：单卷/无卷后缀书（doc_id == series_id，如用户上传的单本）也要命中——
    # 仅用 ``LIKE '{series}__vol%'`` 会漏掉它们导致 vector 路恒零召回（旧缺陷）。
    series = filters.get("series") or filters.get("series_id")
    if series and not doc_prefix:
        s = sql_escape(str(series))
        parts.append(f"(doc_id = '{s}' OR doc_id LIKE '{s}__vol%')")
    # doc_ids：卷级白名单（精确 IN），可与 series 叠加（交集语义）。
    doc_ids = filters.get("doc_ids")
    if doc_ids:
        ids = [str(x) for x in doc_ids if str(x).strip()]
        if ids:
            in_clause = ", ".join(f"'{sql_escape(i)}'" for i in ids[:50])
            parts.append(f"doc_id IN ({in_clause})")
    return parts
