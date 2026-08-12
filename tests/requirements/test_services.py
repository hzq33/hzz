"""需求域补充 — 应用编排服务层（services）薄转发验证。

覆盖 docs/REQUIREMENTS.md 对应需求：H-01/H-04（story_analysis / graph_rag）、
E-06/E-08（character build / merge）。
目的：验证 routers/tools/jobs 唯一入口（application 层 service）签名稳定、
行为与 domain 一致（薄转发不漂移）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.application.novel.services.character_build_service import enqueue_builds
from src.application.novel.services.character_merge_service import merge, suggest_merges
from src.application.novel.services.graph_rag_service import build_graph_rag
from src.application.novel.services.story_analysis_service import run_story_analysis
from tests.conftest import FakeLLM, make_store


# ── H-01 StoryAnalysisService ─────────────────────────────────


async def test_story_analysis_service_requires_catalog():
    """H-01：service 透传 domain 校验（无 catalog → ValueError，不 500）。"""
    store = make_store()
    with pytest.raises(ValueError):
        await run_story_analysis(series_id="不存在系列", store=store, llm_client=None)


# ── H-04 GraphRagService ─────────────────────────────────────


async def test_graph_rag_service_llm_failure_fallback():
    """H-04：service 转发 build_graph_rag——LLM 失败回退规则摘要。"""
    llm = FakeLLM(exc=RuntimeError("llm down"))
    snapshot = SimpleNamespace(
        content_fingerprint="fp-svc-1",
        doc_ids=["S__vol01"],
        relations=[
            SimpleNamespace(
                source="利姆露",
                target="维鲁多拉",
                relation_type="伙伴",
                polarity="positive",
                confidence=0.9,
            )
        ],
        events=[],
    )
    payload = await build_graph_rag(
        "S",
        snapshot=snapshot,
        llm_client=llm,
        force=True,
    )
    assert payload.get("series_id") == "S"
    assert all(c.get("summary") for c in (payload.get("communities") or []))


# ── E-06 CharacterBuildService ────────────────────────────────


async def test_build_service_empty_names_returns_empty():
    """E-06：service 转发 enqueue_builds——空 names 返回空列表（不崩）。"""
    store = make_store()
    jobs = await enqueue_builds(series_id="S", names=[], store=store, wait=True)
    assert jobs == []


# ── E-08 CharacterMergeService ────────────────────────────────


def test_merge_service_empty_series_suggests_nothing():
    """E-08：service 转发 suggest_merges——空系列无建议。"""
    assert suggest_merges("不存在系列") == []


def test_merge_service_unknown_series_raises_valueerror():
    """E-08：service 转发 merge——不存在的系列拒绝合并（ValueError，不创建幽灵数据）。"""
    with pytest.raises(ValueError):
        merge("不存在系列", "利姆露", ["利姆露", "利姆路"])
