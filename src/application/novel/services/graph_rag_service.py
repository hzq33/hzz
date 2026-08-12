"""GraphRagService — GraphRAG 全局层的应用层入口（薄封装）。

统一暴露构建（build_graph_rag）与读取（load_graph_rag / is_stale /
format_global_context），调用方不再直连 domain 的 graph_rag 模块。
"""

from __future__ import annotations

from typing import Any


def load_graph_rag(series_id: str) -> dict[str, Any] | None:
    """读取持久化的 GraphRAG 负载（社区摘要 JSON | None）。"""
    from src.domain.novel.graph_rag import load_graph_rag as _load

    return _load(series_id)


def is_stale(series_id: str) -> bool:
    """graph_rag 是否因 story_analysis 内容变更而失效（fingerprint 不匹配）。"""
    from src.domain.novel.graph_rag import is_stale as _stale

    return _stale(series_id)


def format_global_context(series_id: str, query: str) -> str:
    """包装为检索隔离格式的全局问答上下文。"""
    from src.domain.novel.graph_rag import format_global_context as _format

    return _format(series_id, query)


async def build_graph_rag(
    series_id: str,
    *,
    snapshot,
    llm_client=None,
    force: bool = False,
    on_progress=None,
) -> dict[str, Any]:
    """构建社区摘要并持久化（LLM 失败回退规则摘要）。"""
    from src.domain.novel.graph_rag import build_graph_rag as _build

    return await _build(
        series_id,
        snapshot=snapshot,
        llm_client=llm_client,
        force=force,
        on_progress=on_progress,
    )
