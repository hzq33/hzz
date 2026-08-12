"""需求域 D — 检索：四通道 / hybrid / rerank / Parent 展开 / 过滤 / 图谱富集 / 注入防护。

覆盖 docs/REQUIREMENTS.md D-01 ~ D-07。
黑盒：走公开检索接口（NovelRetrieval / NovelVectorStore / chunker），
构造合成块，不断言实现细节。
"""

from __future__ import annotations

import pytest

from src.application.novel.retrieval import NovelRetrieval
from tests.conftest import index_blocks, make_block, make_store


def _dialogue_block(global_id: str, doc_id: str, text: str, **kw):
    from src.domain.novel.models import DialogueTurn

    return make_block(
        global_id,
        doc_id,
        "dialogue",
        vec_text_dialogue=text,
        dialogues=[DialogueTurn(turn=1, speaker="利姆露", content="（示例台词）")],
        **kw,
    )


def _hier_blocks(text: str, doc_id: str = "S__vol01", known: list[str] | None = None):
    """用公开 HierarchicalChunker 生成 Parent+Child 块（Child 可向量、Parent 举证）。"""
    from src.domain.novel.chunker import CleanedMD, chunk_narrative_for_ingest

    cleaned = CleanedMD(text=text, chapter_title="第一章", source_prefix="《S》")
    return chunk_narrative_for_ingest(
        cleaned,
        doc_id=doc_id,
        hierarchy={
            "enabled": True,
            "parent_chars": 200,
            "parent_overlap_chars": 20,
            "child_chars": 80,
            "min_child_chars": 40,
            "max_child_chars": 120,
            "index_parents": False,
            "chapter_prefix_in_vec": True,
        },
        known_characters=known,
    )


def _retrieval(store, **kw) -> NovelRetrieval:
    return NovelRetrieval(store, **kw)


# ── D-01 四通道 ───────────────────────────────────────────────


async def test_narrative_channel_hits(store_with_narrative):
    """D-01：narrative 通道返回场景/情节块。"""
    intent, hits = await _retrieval(store_with_narrative).search_raw("温泉")
    assert hits, "应命中 narrative 块"
    assert any(h.channel == "narrative" for h in hits)


async def test_dialogue_channel_hits():
    """D-01：dialogue 通道返回台词块（口吻模仿素材，经独立风格检索直查）。

    设计变更（2026-08）：dialogue 移出事实混合检索——dialogue 是 narrative 的
    对话视图，事实检索由 narrative 原文覆盖；口吻/模仿素材由独立风格检索
    （store.search channel=dialogue）承担，不再经 search_raw。
    """
    store = make_store()
    await index_blocks(store, [_dialogue_block("d1", "S__vol01", "「开饭了！」八奈见杏菜大声说。")])
    # 风格/模仿素材：独立 dialogue 直查（原 search_raw 混合路径已移除 dialogue）
    hits = await store.search("开饭", channel="dialogue", doc_id="S__vol01")
    assert hits
    assert any(h.channel == "dialogue" for h in hits)


async def test_channels_isolated():
    """D-01：narrative 查询以 narrative 通道为主（不串入 dialogue 块）。"""
    store = make_store()
    await index_blocks(
        store,
        [
            make_block("n1", "S__vol01", "narrative", vec_text_narrative="利姆露在温泉里泡澡"),
            _dialogue_block("d1", "S__vol01", "「开饭了！」八奈见杏菜大声说。"),
        ],
    )
    intent, hits = await _retrieval(store).search_raw("温泉")
    assert hits
    narrative_hits = [h for h in hits if h.channel == "narrative"]
    dialogue_hits = [h for h in hits if h.channel == "dialogue"]
    assert narrative_hits, "narrative 查询应命中 narrative 块"
    # 若 dialogue 块混入，分数不得高于 narrative 命中（主通道优先）
    if dialogue_hits:
        assert max(h.score for h in narrative_hits) >= max(
            h.score for h in dialogue_hits
        )


# ── D-02 hybrid 检索 ──────────────────────────────────────────


async def test_hybrid_search_returns_ranked_hits():
    """D-02：hybrid 检索返回排序结果（相关性分数存在）。"""
    store = make_store()
    await index_blocks(
        store,
        [
            make_block("n1", "S__vol01", "narrative", vec_text_narrative="利姆露和维鲁多拉在温泉"),
            make_block("n2", "S__vol01", "narrative", vec_text_narrative="朱菜在厨房做饭"),
        ],
    )
    intent, hits = await _retrieval(store).search_raw("温泉")
    assert hits
    assert all(getattr(h, "score", None) is not None for h in hits)
    # 相关块应排在无关块之前
    scores = [h.score for h in hits]
    assert scores == sorted(scores, reverse=True)


async def test_search_unknown_query_graceful():
    """D-02：完全无关查询不 500，返回可读结果或空。"""
    store = make_store()
    await index_blocks(store, [make_block("n1", "S__vol01", "narrative", vec_text_narrative="温泉")])
    r = await _retrieval(store).search("量子力学弦理论")
    assert isinstance(r, str)


# ── D-03 精排回退 ─────────────────────────────────────────────


async def test_reranker_fallback_identity():
    """D-03：无可用模型权重时精排回退（Identity），检索仍正常。"""
    from src.infrastructure.reranker import IdentityReranker

    store = make_store()
    await index_blocks(store, [make_block("n1", "S__vol01", "narrative", vec_text_narrative="温泉")])
    r = NovelRetrieval(store, reranker=IdentityReranker(), graph_enrich=False)
    intent, hits = await r.search_raw("温泉")
    assert hits


# ── D-04 Narrative Child→Parent 展开 ──────────────────────────


async def test_child_hit_expands_to_parent_evidence():
    """D-04：命中 Child 时举证展开到 Parent 原文（输出含父块语境）。"""
    store = make_store()
    text = (
        "利姆露从温泉中起身，热气蒸腾。"
        "维鲁多拉盘踞在池边，懒洋洋地打了个哈欠。"
        "朱菜端着茶水走了过来，笑着说：欢迎回来。"
        "利姆露点了点头，接过茶杯。"
    ) * 4
    blocks = _hier_blocks(text, doc_id="S__vol01")
    parents = [b for b in blocks if getattr(b, "granularity", "") == "parent"]
    children = [b for b in blocks if getattr(b, "granularity", "") == "child"]
    assert parents, "应产出 Parent 块"
    assert children, "应产出 Child 块"
    await index_blocks(store, blocks)
    out = await _retrieval(store).search("维鲁多拉")
    assert "维鲁多拉" in out


async def test_legacy_block_without_parent_graceful():
    """D-04：旧库块（无 parent_id）命中即举证，展开 no-op 不报错。"""
    store = make_store()
    await index_blocks(
        store,
        [make_block("legacy1", "S__vol01", "narrative", vec_text_narrative="利姆露在温泉")],
    )
    out = await _retrieval(store).search("温泉")
    assert isinstance(out, str)


async def test_expand_respects_chapter_boundary():
    """D-04：展开不跨章（不同章 Parent 不串入举证）。"""
    store = make_store()
    # 第一章：多角色温泉场景，多个高相关块填满 top_k
    blocks_a = _hier_blocks(
        "利姆露在温泉里泡澡。维鲁多拉在温泉边打盹。朱菜端着茶走向温泉。"
        "加鲁鲁在温泉旁玩耍。苍影在温泉附近站岗。" * 20,
        doc_id="S__vol01",
    )
    # 第二章：完全无关字符集，0 相似度 → 不进 top_k
    from src.domain.novel.chunker import CleanedMD, chunk_narrative_for_ingest

    cleaned_b = CleanedMD(
        text="Chapter two totally different content. " * 30,
        chapter_title="第二章",
        source_prefix="《S》",
    )
    blocks_b = chunk_narrative_for_ingest(
        cleaned_b,
        doc_id="S__vol01",
        chapter_index=1,
        hierarchy={
            "enabled": True,
            "parent_chars": 200,
            "parent_overlap_chars": 20,
            "child_chars": 80,
            "min_child_chars": 40,
            "max_child_chars": 120,
            "index_parents": False,
            "chapter_prefix_in_vec": True,
        },
    )
    await index_blocks(store, blocks_a + blocks_b)
    out = await _retrieval(store).search("温泉")
    # 第一章 Parent 展开发生（长文本被作为举证输出）
    assert out.count("利姆露在温泉") >= 15
    # 第二章完全不进入检索结果（跨章不串入）
    assert "totally different" not in out


# ── D-05 过滤 ─────────────────────────────────────────────────


async def test_series_filter_excludes_other_series():
    """D-05：series 过滤只返回该系列块。"""
    store = make_store()
    await index_blocks(
        store,
        [
            make_block("a1", "A__vol01", "narrative", vec_text_narrative="利姆露在温泉"),
            make_block("b1", "B__vol01", "narrative", vec_text_narrative="利姆露在温泉"),
        ],
    )
    intent, hits = await _retrieval(store).search_raw("温泉", series_id="A")
    assert hits
    assert all(h.block.doc_id.startswith("A") for h in hits)


async def test_single_volume_series_filter():
    """D-05：单卷书（doc_id == series_id，无 __vol 后缀）也能被系列过滤命中。"""
    store = make_store()
    await index_blocks(store, [make_block("s1", "单卷书", "narrative", vec_text_narrative="利姆露在温泉")])
    intent, hits = await _retrieval(store).search_raw("温泉", series_id="单卷书")
    assert hits


async def test_doc_ids_filter():
    """D-05：doc_ids 白名单过滤只返回指定卷。"""
    store = make_store()
    await index_blocks(
        store,
        [
            make_block("v1", "S__vol01", "narrative", vec_text_narrative="利姆露在温泉"),
            make_block("v2", "S__vol02", "narrative", vec_text_narrative="利姆露在温泉"),
        ],
    )
    intent, hits = await _retrieval(store).search_raw("温泉", doc_ids=["S__vol01"])
    assert hits
    assert all(h.block.doc_id == "S__vol01" for h in hits)


async def test_deleted_volume_not_retrievable():
    """D-05：删除卷后该卷块不再出现在检索结果。"""
    store = make_store()
    await index_blocks(
        store,
        [
            make_block("v1", "S__vol01", "narrative", vec_text_narrative="利姆露在温泉"),
            make_block("v2", "S__vol02", "narrative", vec_text_narrative="利姆露在温泉"),
        ],
    )
    await store.delete_by_doc_id("S__vol01")
    intent, hits = await _retrieval(store).search_raw("温泉")
    assert all(h.block.doc_id != "S__vol01" for h in hits)


# ── D-06 图谱富集 ─────────────────────────────────────────────


async def test_graph_enrich_missing_graceful(tmp_path):
    """D-06：图谱数据缺失时检索不崩溃（优雅降级）。"""
    store = make_store()
    await index_blocks(store, [make_block("n1", "S__vol01", "narrative", vec_text_narrative="温泉")])
    r = NovelRetrieval(store, graph_enrich=True, graph_dir=tmp_path / "no-graphs")
    out = await r.search("温泉")
    assert isinstance(out, str)


# ── D-07 提示注入防护 ─────────────────────────────────────────


async def test_search_results_isolation_marker():
    """D-07：检索结果包在 <search_results> 隔离标记 + 不可信警示中。"""
    store = make_store()
    await index_blocks(store, [make_block("n1", "S__vol01", "narrative", vec_text_narrative="温泉")])
    out = await _retrieval(store).search("温泉")
    assert "<search_results>" in out
    assert "</search_results>" in out
    assert "不可信" in out or "不要执行" in out or "绝对不要执行" in out


# ── fixtures ──────────────────────────────────────────────────


@pytest.fixture()
async def store_with_narrative():
    store = make_store()
    await index_blocks(
        store,
        [
            make_block("n1", "S__vol01", "narrative", vec_text_narrative="利姆露在温泉里泡澡，十分惬意"),
            make_block("n2", "S__vol01", "narrative", vec_text_narrative="朱菜在厨房做饭，香味四溢"),
        ],
    )
    return store


# ── D-05 补充：过滤子句契约 ──────────────────────────────────


def test_metadata_prefilter_characters_or_grouped():
    """D-05：多角色过滤 OR 分组（AND 语义会导致零召回）。"""
    from src.infrastructure.lance_filters import metadata_prefilter_clauses

    clauses = metadata_prefilter_clauses({"characters": ["利姆露", "维鲁多拉"]})
    assert len(clauses) == 1
    assert " OR " in clauses[0]
    assert " AND " not in clauses[0]


def test_metadata_prefilter_sql_escape():
    """D-05：过滤值 SQL 转义（引号注入防护）。"""
    from src.infrastructure.lance_filters import metadata_prefilter_clauses, sql_escape

    assert sql_escape("O'Brien") == "O''Brien"
    clauses = metadata_prefilter_clauses({"characters": ["O'Brien"]})
    assert clauses and "O''Brien" in clauses[0]
