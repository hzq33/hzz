"""StoryAnalysisService — 剧情分析管线的应用层入口（薄封装）。

统一暴露编排（run_story_analysis）与读取（load_analysis / load_timeline /
load_lorebook / max_tokens / build_relation_graph），调用方（routers / tools /
jobs / core）不再直连 domain 的 story_analysis 包或其子模块，
也消除了 ``story_analysis`` 与 ``story_analysis.config`` 双 import 路径。
"""

from __future__ import annotations

from typing import Any


async def run_story_analysis(
    *,
    series_id: str,
    store,
    llm_client=None,
    doc_id: str | None = None,
    force: bool = False,
    max_chapters: int | None = None,
    map_concurrency: int | None = None,
    map_max_chars: int | None = None,
    extract_foreshadows: bool | None = None,
    on_progress=None,
) -> Any:
    """运行剧情分析管线（map-reduce 全量分析），返回 StoryAnalysisSnapshot。"""
    from src.domain.novel.story_analysis import run_story_analysis as _run

    return await _run(
        series_id=series_id,
        store=store,
        llm_client=llm_client,
        doc_id=doc_id,
        force=force,
        max_chapters=max_chapters,
        map_concurrency=map_concurrency,
        map_max_chars=map_max_chars,
        extract_foreshadows=extract_foreshadows,
        on_progress=on_progress,
    )


def load_analysis(series_id: str) -> Any:
    """读取剧情分析快照（StoryAnalysisSnapshot | None）。"""
    from src.domain.novel.story_analysis import load_analysis as _load

    return _load(series_id)


def load_timeline(series_id: str) -> dict[str, Any] | None:
    """读取编年体时间线（chronicle/by_character/by_era）。"""
    from src.domain.novel.story_analysis.timeline import load_timeline as _load

    return _load(series_id)


def load_lorebook(series_id: str) -> dict[str, Any] | None:
    """读取时间感知设定书（entries）。"""
    from src.domain.novel.story_analysis.lorebook import load_lorebook as _load

    return _load(series_id)


def story_analysis_max_tokens() -> int:
    """剧情分析 LLM 输出上限（配置）。"""
    from src.domain.novel.story_analysis import story_analysis_max_tokens as _m

    return _m()


def build_relation_graph(
    relations: list[Any],
    *,
    min_confidence: float = 0.0,
    min_weight: int = 1,
) -> dict[str, Any]:
    """聚合 RelationChange 记录为角色关系图 {nodes, edges, stats}。"""
    from src.domain.novel.relation_graph import build_relation_graph as _build

    return _build(
        relations,
        min_confidence=min_confidence,
        min_weight=min_weight,
    )
