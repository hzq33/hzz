"""需求域 H — 世界体系：剧情分析 / 时间线 / 设定书 / GraphRAG / 全局意图。

覆盖 docs/REQUIREMENTS.md H-01 ~ H-05。
黑盒：GraphRAG 走公开函数（detect_communities / build_graph_rag /
format_global_context），API 走公开端点。
"""

from __future__ import annotations

from types import SimpleNamespace

import networkx as nx
import pytest

from src.application.novel.intent_router import IntentRouter
from src.domain.novel.graph_rag import (
    build_graph_rag,
    detect_communities,
    format_global_context,
    load_graph_rag,
    search_global,
)
from tests.conftest import FakeLLM, auth_headers


def _rel(source: str, target: str, rtype: str = "伙伴", confidence: float = 0.9):
    return SimpleNamespace(
        source=source,
        target=target,
        relation_type=rtype,
        polarity="positive",
        confidence=confidence,
    )


def _snapshot(relations: list | None = None):
    return SimpleNamespace(
        content_fingerprint="fp-test-1",
        doc_ids=["S__vol01"],
        relations=relations or [],
        events=[],
    )


# ── H-03/H-04 GraphRAG 数据层 ────────────────────────────────


def test_detect_communities_two_communities():
    """H-04：社区发现按边权重划分（双社区）。"""
    G = nx.Graph()
    G.add_edge("利姆露", "维鲁多拉", weight=5)
    G.add_edge("利姆露", "朱菜", weight=4)
    G.add_edge("朱菜", "兰加", weight=3)
    G.add_edge("温水和彦", "八奈见杏菜", weight=5)
    G.add_edge("温水和彦", "烧盐柠檬", weight=4)
    communities = detect_communities(G)
    assert len(communities) == 2, "应划分出两个社区"
    members = {frozenset(c) for c in communities}
    assert any("利姆露" in m and "维鲁多拉" in m for m in members)
    assert any("温水和彦" in m and "八奈见杏菜" in m for m in members)


def test_detect_communities_isolated_singletons():
    """H-04：孤立节点自成一社区。"""
    G = nx.Graph()
    G.add_edge("利姆露", "朱菜", weight=3)
    G.add_node("路人甲")
    G.add_node("路人乙")
    communities = detect_communities(G)
    members = {frozenset(c) for c in communities}
    assert any(c == frozenset({"路人甲"}) for c in members)
    assert any(c == frozenset({"路人乙"}) for c in members)


def test_detect_communities_empty_graph():
    """H-04：空图返回空列表（不崩）。"""
    assert detect_communities(nx.Graph()) == []


async def test_build_graph_rag_rule_fallback_on_llm_failure():
    """H-04：LLM 摘要失败时回退规则摘要，构建仍成功。"""
    llm = FakeLLM(exc=RuntimeError("llm down"))
    payload = await build_graph_rag(
        "S",
        snapshot=_snapshot([_rel("利姆露", "维鲁多拉"), _rel("利姆露", "朱菜")]),
        llm_client=llm,
        force=True,
    )
    assert payload.get("series_id") == "S"
    communities = payload.get("communities") or []
    assert communities, "应有社区"
    assert all(c.get("summary") for c in communities), "回退摘要应非空"
    assert payload.get("fingerprint") == "fp-test-1"


async def test_build_graph_rag_caches_same_fingerprint():
    """H-04：同 fingerprint 非 force 直接返回缓存（不重复构建）。"""
    llm = FakeLLM()
    p1 = await build_graph_rag(
        "S",
        snapshot=_snapshot([_rel("利姆露", "朱菜")]),
        llm_client=llm,
        force=True,
    )
    calls_after_first = llm.calls
    p2 = await build_graph_rag(
        "S",
        snapshot=_snapshot([_rel("利姆露", "朱菜")]),
        llm_client=llm,
        force=False,
    )
    assert llm.calls == calls_after_first, "命中缓存不应再调 LLM"
    assert p2.get("fingerprint") == p1.get("fingerprint")


async def test_global_search_without_data_returns_empty():
    """H-04：无全局层数据时检索返回空（不崩）。"""
    result = search_global("不存在的系列", "主线")
    assert result is None or result == "" or result == []
    ctx = format_global_context("不存在的系列", "主线")
    assert ctx is None or ctx == ""


# ── H-01/H-02 API 层（剧情分析 / 时间线 / 设定书）─────────────


def test_story_analysis_missing_series(api_client):
    """H-01：无分析数据的系列 → 200 + exists=false（不 500）。"""
    r = api_client.get(
        "/api/v1/agent/story-analysis",
        params={"series_id": "不存在系列"},
        headers=auth_headers(),
    )
    assert r.status_code == 200
    assert r.json().get("exists") is False


def test_story_analysis_build_requires_series(api_client):
    """H-01：build 请求缺 series_id → 422。"""
    r = api_client.post(
        "/api/v1/agent/story-analysis/build",
        json={},
        headers=auth_headers(),
    )
    assert r.status_code == 422


def test_story_analysis_job_not_found_404(api_client):
    """H-01：查询不存在的分析 Job → 404。"""
    r = api_client.get(
        "/api/v1/agent/story-analysis/jobs/no-such-job",
        headers=auth_headers(),
    )
    assert r.status_code == 404


def test_timeline_missing_series(api_client):
    """H-02：无时间线数据 → 200 + exists=false。"""
    r = api_client.get(
        "/api/v1/agent/timeline",
        params={"series_id": "不存在系列"},
        headers=auth_headers(),
    )
    assert r.status_code == 200
    assert r.json().get("exists") is False


def test_lorebook_missing_series(api_client):
    """H-02：无设定书数据 → 200 + exists=false。"""
    r = api_client.get(
        "/api/v1/agent/lorebook",
        params={"series_id": "不存在系列"},
        headers=auth_headers(),
    )
    assert r.status_code == 200
    assert r.json().get("exists") is False


def test_rag_global_missing_series(api_client):
    """H-04：无全局层数据的系列 → 200 + exists=false（不 500）。"""
    r = api_client.get(
        "/api/v1/agent/rag-global",
        params={"series_id": "不存在系列", "query": "主线"},
        headers=auth_headers(),
    )
    assert r.status_code == 200
    assert r.json().get("exists") is False


def test_rag_global_build_requires_series(api_client):
    """H-04：rag-global build 请求缺 series_id → 422。"""
    r = api_client.post(
        "/api/v1/agent/rag-global/build",
        json={},
        headers=auth_headers(),
    )
    assert r.status_code == 422


# ── H-05 全局意图路由 ─────────────────────────────────────────


def test_global_intent_queries():
    """H-05：主线/主题/梗概类查询 → 全局意图。"""
    router = IntentRouter()
    for q in ("这本书的主线是什么", "概括一下剧情", "整部书讲了什么", "人物关系网如何"):
        assert router.classify(q).is_global is True, f"{q} 应走全局层"


def test_local_intent_queries():
    """H-05：实体/事件/口吻类查询 → 本地检索。"""
    router = IntentRouter()
    for q in ("利姆露在哪里", "模仿利姆露的语气说话", "朱菜说了什么"):
        assert router.classify(q).is_global is False, f"{q} 应走本地检索"


# ── H-02/H-03 补充：时间线构建 + 关系图谱 ─────────────────────


def test_build_relation_graph_merges_edges():
    """H-03：关系快照聚合成图（节点/边/统计）。"""
    from src.domain.novel.relation_graph import build_relation_graph
    from src.domain.novel.story_analysis.models import RelationChange

    relations = [
        RelationChange(
            change_id="r1", source="利姆露", target="维鲁多拉",
            relation_type="伙伴", polarity="positive", confidence=0.9, chapter_order=1,
        ),
        RelationChange(
            change_id="r2", source="利姆露", target="朱菜",
            relation_type="主从", polarity="positive", confidence=0.8, chapter_order=2,
        ),
    ]
    graph = build_relation_graph(relations)
    assert graph.get("nodes")
    assert len(graph.get("edges", [])) >= 2
    assert graph.get("stats", {}).get("node_count", 0) >= 3


def test_build_chronicle_empty_snapshot():
    """H-02：空快照构建时间线 → 空 chronicle + 结构完整（不崩）。"""
    from src.domain.novel.story_analysis.models import StoryAnalysisSnapshot
    from src.domain.novel.story_analysis.timeline import build_chronicle

    snapshot = StoryAnalysisSnapshot(series_id="S", content_fingerprint="fp")
    result = build_chronicle(snapshot)
    assert result.get("series_id") == "S"
    assert result.get("chronicle") == []


def test_build_chronicle_with_event():
    """H-02：有事件时时间线产出条目（seq/summary/角色索引）。"""
    from src.domain.novel.story_analysis.models import StoryAnalysisSnapshot, StoryEvent
    from src.domain.novel.story_analysis.timeline import build_chronicle

    snapshot = StoryAnalysisSnapshot(
        series_id="S",
        events=[
            StoryEvent(
                event_id="e1", summary="利姆露与维鲁多拉相遇",
                event_type="plot", characters=["利姆露", "维鲁多拉"],
                story_time={"year": 1, "label": "第一年"},
            )
        ],
    )
    result = build_chronicle(snapshot)
    assert len(result.get("chronicle", [])) == 1
    assert result["chronicle"][0]["summary"] == "利姆露与维鲁多拉相遇"
    assert result.get("by_character", {}).get("利姆露")
