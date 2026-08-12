"""需求域 E — 角色管线：盘点 / 聚类 / 归一校验 / seed / 跨卷衰减 / 建卡 / 图谱 / 名录。

覆盖 docs/REQUIREMENTS.md E-01 ~ E-08。
黑盒：领域层走公开函数（cluster_mentions / harvest_chapter_names /
build_llm_seed / persist_inventory_candidates），API 层走公开端点。
"""

from __future__ import annotations

import asyncio

import pytest

from src.domain.novel.character_inventory.candidates import (
    build_llm_seed,
    load_inventory_candidates,
    persist_inventory_candidates,
)
from src.domain.novel.character_inventory.models import (
    InventoryCharacter,
    InventoryResult,
)
from src.domain.novel.character_ner import Mention, cluster_mentions
from tests.conftest import auth_headers

_HARVEST_TEXT = (
    "八奈见杏菜说：「开饭了。」烧盐笑着点头。"
    "「利姆露大人，您醒了吗。」维鲁多拉问道。"
    "利姆露大人回答：「醒着呢。」"
    "他说：「别管我了。」"
)


class _FakeLLM:
    def __init__(self, payload: str | None = None, *, exc: Exception | None = None):
        self.payload = payload
        self.exc = exc

    async def achat(self, messages, **kwargs) -> str:
        if self.exc:
            raise self.exc
        return self.payload or ""


def _harvest(payload: str | None, **kw):
    from src.application.novel.dialogue_pipeline.harvest import harvest_chapter_names

    return asyncio.run(harvest_chapter_names(_HARVEST_TEXT, _FakeLLM(payload), **kw))


def _inv(name: str, mentions: int, importance: str = "supporting") -> InventoryCharacter:
    return InventoryCharacter(
        canonical_name=name,
        aliases=[],
        importance=importance,
        mention_count=mentions,
    )


def _inv_result(*chars: InventoryCharacter, llm_skipped: bool = False) -> InventoryResult:
    return InventoryResult(
        characters=list(chars),
        llm_skipped=llm_skipped,
        draft_clusters=0,
    )


# ── E-02 聚类规则 ─────────────────────────────────────────────


def test_cluster_typo_edit1_merged():
    """E-02：等长错字（编辑距离 1）合并为同一簇（利姆路/利姆露）。"""
    mentions = [
        Mention("利姆路", 0, 3, "cluener", 0.9),
        Mention("利姆露", 10, 13, "cluener", 0.9),
    ]
    clusters = cluster_mentions(mentions, min_mentions=1)
    assert len(clusters) == 1
    assert {"利姆路", "利姆露"} == set(clusters[0].surfaces)


def test_cluster_fullname_vs_shortname_not_premerged():
    """E-02：全名 vs 短名（长度差≥2）不预合并（交给 LLM 归一裁决）。"""
    mentions = [
        Mention("温水佳树", 0, 4, "cluener", 0.9),
        Mention("温水", 10, 12, "cluener", 0.9),
    ]
    clusters = cluster_mentions(mentions, min_mentions=1)
    assert len(clusters) == 2


def test_cluster_noise_filtered():
    """E-02：噪声 mention（他说/单字）被过滤，不进候选。"""
    mentions = [
        Mention("利姆露", 0, 3, "cluener", 0.9),
        Mention("他说", 10, 12, "cluener", 0.9),
        Mention("来", 20, 21, "cluener", 0.9),
    ]
    clusters = cluster_mentions(mentions, min_mentions=1)
    surfaces = {s for c in clusters for s in c.surfaces}
    assert "利姆露" in surfaces
    assert "他说" not in surfaces
    assert "来" not in surfaces


def test_cluster_min_mentions_filter():
    """E-02：min_cluster_mentions 以下的稀疏短名被过滤（长名有稀有保护）。"""
    mentions = [
        Mention("利姆露", 0, 3, "cluener", 0.9),
        Mention("利姆露", 10, 13, "cluener", 0.9),
        Mention("路人", 20, 22, "cluener", 0.9),
    ]
    clusters = cluster_mentions(mentions, min_mentions=2)
    surfaces = {s for c in clusters for s in c.surfaces}
    assert "利姆露" in surfaces
    assert "路人" not in surfaces


# ── E-03 LLM 归一校验（幻觉防御）──────────────────────────────


def test_harvest_keeps_real_names():
    """E-03：LLM 输出的原文存在名字（含敬称变体）保留。"""
    names = _harvest('{"names": ["八奈见杏菜", "烧盐", "维鲁多拉", "利姆露"]}')
    assert names is not None
    assert {"八奈见杏菜", "烧盐", "维鲁多拉", "利姆露"} <= set(names)


def test_harvest_drops_hallucinated_names():
    """E-03：原文不存在的名字被丢弃（幻觉防御）。"""
    names = _harvest('{"names": ["八奈见杏菜", "不存在的路人", "烧盐"]}')
    assert "八奈见杏菜" in names
    assert "烧盐" in names
    assert "不存在的路人" not in names


def test_harvest_filters_noise_and_single_char():
    """E-03：噪声词/单字（他说/他/我知）即使 LLM 输出也过滤。"""
    names = _harvest('{"names": ["八奈见杏菜", "他说", "他", "我知", "烧盐"]}')
    assert "他说" not in names
    assert "他" not in names
    assert "八奈见杏菜" in names


def test_harvest_filters_occupation_noise():
    """E-03：职业/占位词（图书管理员/店员）过滤。"""
    text = "图书管理员说：「别吵。」八奈见杏菜回答：「好的。」"
    from src.application.novel.dialogue_pipeline.harvest import harvest_chapter_names

    names = asyncio.run(
        harvest_chapter_names(text, _FakeLLM('{"names": ["八奈见杏菜", "图书管理员", "店员"]}'))
    )
    assert "八奈见杏菜" in names
    assert "图书管理员" not in names
    assert "店员" not in names


def test_harvest_llm_failure_returns_none():
    """E-03：LLM 失败/不可用时返回 None（调用方回退规则路径）。"""
    from src.application.novel.dialogue_pipeline.harvest import harvest_chapter_names

    r = asyncio.run(harvest_chapter_names(_HARVEST_TEXT, None))
    assert r is None
    r2 = asyncio.run(
        harvest_chapter_names(_HARVEST_TEXT, _FakeLLM(exc=RuntimeError("boom")))
    )
    assert r2 is None
    r3 = asyncio.run(harvest_chapter_names(_HARVEST_TEXT, _FakeLLM("不是 JSON")))
    assert r3 is None


# ── E-04 Seed 策略 ────────────────────────────────────────────


def test_seed_min_mentions_floor():
    """E-04：低于 min_mentions 的角色不入选 seed。"""
    chars = [_inv("利姆露", 10), _inv("朱菜", 3), _inv("路人甲", 1)]
    r = build_llm_seed(chars, seed_min_mentions=2)
    names = {c.canonical_name for c in r.characters}
    assert "利姆露" in names
    assert "路人甲" not in names


def test_seed_blacklist_removed():
    """E-04：物种黑名单（史莱姆等）不入选 seed。"""
    chars = [_inv("史莱姆", 100), _inv("利姆露", 10)]
    r = build_llm_seed(chars, seed_min_mentions=1)
    names = {c.canonical_name for c in r.characters}
    assert "史莱姆" not in names
    assert "利姆露" in names


def test_seed_top_k_cap():
    """E-04：seed 按 top_k 封顶（不会全量进入先验）。"""
    chars = [_inv(f"角色{i}", 100 - i) for i in range(30)]
    r = build_llm_seed(chars, seed_min_mentions=1, config={"seed": {"top_k": 10, "mode": "fixed"}})
    assert len(r.characters) <= 10
    assert r.top_k == 10


def test_seed_orders_by_mentions():
    """E-04：seed 按提及数排序（高频在前）。"""
    chars = [_inv("低频", 2), _inv("高频", 100), _inv("中频", 30)]
    r = build_llm_seed(chars, seed_min_mentions=1, config={"seed": {"top_k": 3, "mode": "fixed"}})
    counts = [c.mention_count for c in r.characters]
    assert counts == sorted(counts, reverse=True)


def test_seed_names_from_inventory(tmp_path):
    """E-01/E-04：无 LLM 时 seed 仍可从聚类草稿产出（llm_skipped 不阻塞）。"""
    from src.domain.novel.character_inventory.candidates import seed_names_from_inventory

    result = _inv_result(
        _inv("利姆露", 10), _inv("朱菜", 3), _inv("路人甲", 1), llm_skipped=True
    )
    names = seed_names_from_inventory(result, seed_min_mentions=2)
    assert "利姆露" in names
    assert "朱菜" in names  # 3 >= 2 入选
    assert "路人甲" not in names  # 1 < 2 剔除


# ── E-05 跨卷合并衰减 ─────────────────────────────────────────


def test_merge_decay_across_volumes():
    """E-05：旧卷提及数按 merge_decay 衰减后与新卷取 max（高频不绑架阈值）。"""
    persist_inventory_candidates(
        series_id="S",
        doc_id="S__vol01",
        inventory=_inv_result(_inv("利姆露", 100)),
        seed_min_mentions=1,
    )
    data1 = load_inventory_candidates("S")
    c1 = next(c for c in data1["candidates"] if c["name"] == "利姆露")
    assert c1["mention_count"] == 100

    # 卷2 出现 5 次：旧 100 → 100*0.85=85，取 max(85, 5)=85
    persist_inventory_candidates(
        series_id="S",
        doc_id="S__vol02",
        inventory=_inv_result(_inv("利姆露", 5)),
        seed_min_mentions=1,
    )
    data2 = load_inventory_candidates("S")
    c2 = next(c for c in data2["candidates"] if c["name"] == "利姆露")
    assert 80 <= c2["mention_count"] <= 90, "应衰减到 85 附近而非保持 100 或降为 5"


# ── E-06 / E-08 API 层（名录 / 候选 / 建卡 Job）────────────────


def test_characters_list_empty(api_client):
    """E-08：空库角色列表 → 200 空结果（不 500）。"""
    r = api_client.get("/api/v1/agent/characters", headers=auth_headers())
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_character_candidates_empty(api_client):
    """E-01/E-08：无盘点数据的候选列表 → 200 + 空 candidates。"""
    r = api_client.get(
        "/api/v1/agent/characters/candidates",
        params={"series_id": "不存在系列"},
        headers=auth_headers(),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["candidates"] == []


def test_character_job_not_found_404(api_client):
    """E-06：查询不存在的建卡 Job → 404（不 500）。"""
    r = api_client.get(
        "/api/v1/agent/characters/jobs/no-such-job",
        headers=auth_headers(),
    )
    assert r.status_code == 404


def test_build_characters_requires_names(api_client):
    """E-06：建卡请求缺 names → 422。"""
    r = api_client.post(
        "/api/v1/agent/characters/build",
        json={"series_id": "S"},
        headers=auth_headers(),
    )
    assert r.status_code == 422


# ── E-07 角色图谱 API ─────────────────────────────────────────


def test_character_graph_empty_series(api_client):
    """E-07：无剧情分析数据的图谱 → 200 + exists=false（不 500）。"""
    r = api_client.get(
        "/api/v1/agent/characters/graph",
        params={"series_id": "不存在系列"},
        headers=auth_headers(),
    )
    assert r.status_code == 200
    body = r.json()
    assert body.get("exists") is False


# ── E-08 Alias 名录 CRUD ──────────────────────────────────────


def test_alias_roster_404_when_missing(api_client):
    """E-08：无名录的系列 GET roster → 404。"""
    r = api_client.get(
        "/api/v1/agent/characters/roster",
        params={"series_id": "不存在系列"},
        headers=auth_headers(),
    )
    assert r.status_code == 404


def test_alias_roster_roundtrip(api_client):
    """E-08：PUT roster 写入后 GET 可读回（roundtrip）。"""
    payload = {
        "entities": [
            {
                "character_id": "S__rimuru",
                "canonical_name": "利姆露",
                "aliases": ["利姆路", "头目"],
            }
        ]
    }
    r = api_client.put(
        "/api/v1/agent/characters/roster",
        params={"series_id": "S"},
        json=payload,
        headers=auth_headers(),
    )
    assert r.status_code == 200
    r2 = api_client.get(
        "/api/v1/agent/characters/roster",
        params={"series_id": "S"},
        headers=auth_headers(),
    )
    assert r2.status_code == 200
    entities = r2.json().get("entities", [])
    assert any(e.get("canonical_name") == "利姆露" for e in entities)


def test_alias_roster_series_list(api_client):
    """E-08：roster/series 列出已有名录的系列。"""
    r = api_client.get(
        "/api/v1/agent/characters/roster/series",
        headers=auth_headers(),
    )
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body.get("series"), list)


# ── E-06 补充：建卡落盘 ───────────────────────────────────────


def test_persist_card_writes_json(tmp_path):
    """E-06：persist_card 落盘角色卡 JSON（字段完整、可读回）。"""
    import json

    from src.domain.novel.character_on_demand.models import EvidencePack
    from src.domain.novel.character_on_demand.persist import persist_card

    evidence = EvidencePack(
        dialogues=[{"speaker": "利姆露", "content": "你好"}],
        narratives=["利姆露在温泉"],
        dialogue_hits=1,
        narrative_hits=1,
    )
    card, path = persist_card(
        series_id="S",
        character_id="S__rimuru",
        canonical_name="利姆露",
        aliases=["利姆路"],
        profile={"speech": {"greeting": "你好"}, "traits": {"kindness": 0.9}},
        evidence=evidence,
        source_doc_ids=["S__vol01"],
        low_evidence=False,
    )
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["profile"]["source_work"] == "S"
    assert data["profile"]["name"] == "利姆露"
