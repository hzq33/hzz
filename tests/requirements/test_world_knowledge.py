"""WorldKnowledgeService — 建表与查询验证（懒构建 + 精确过滤 + alias 扩展）。

覆盖设计（docs/WORLD_KNOWLEDGE_TOOL_DESIGN.md v5）：
- 懒构建：源 JSON 缺失 → 空结果不报错
- 精确过滤：relations/events/timeline/lorebook 查询按条件命中
- alias 扩展：查询词命中 canonical 变体
- 工具：builtin_world_knowledge 参数化查询 + 结果格式化
"""

from __future__ import annotations

import json

import pytest

import src.application.novel.services.world_knowledge_service as svc
from src.tools.builtin_world_knowledge import WorldKnowledgeTool


@pytest.fixture
def world_kb(tmp_path, monkeypatch):
    """重定向 SQLite 到临时目录 + 写入合成 JSON 源。"""
    monkeypatch.setattr(svc, "_DB_PATH", tmp_path / "world_kb.sqlite")

    # 合成 story_analyses JSON
    analysis = {
        "series_id": "S",
        "relations": [
            {
                "change_id": "rc_1",
                "source": "利姆露",
                "target": "维鲁多拉",
                "relation_type": "伙伴",
                "polarity": "positive",
                "confidence": 0.9,
                "chapter_order": 2,
                "doc_id": "S__vol01",
                "chapter_title": "第2章",
                "summary": "两人成为伙伴。",
                "evidence": [{"block_id": "S__vol01_c002_n0000__s000"}],
                "story_time": {"period": "转生后"},
            }
        ],
        "events": [
            {
                "event_id": "ev_1",
                "summary": "利姆露苏醒。",
                "event_type": "plot",
                "characters": ["利姆露"],
                "confidence": 0.95,
                "chapter_order": 1,
                "doc_id": "S__vol01",
                "chapter_title": "第1章",
                "evidence": [{"block_id": "S__vol01_c001_n0000__s000"}],
                "story_time": {"period": "转生后"},
            }
        ],
    }
    timeline = {
        "chronicle": [
            {"seq": 1, "summary": "利姆露苏醒。", "event_type": "plot",
             "characters": ["利姆露"], "doc_id": "S__vol01", "chapter_order": 1,
             "chapter_title": "第1章", "story_time": {"period": "转生后"}},
        ],
        "by_character": {"利姆露": [1]},
    }
    lorebook = {
        "entries": [
            {"entry_id": "lb_1", "entity": "维鲁多拉", "keys": ["维鲁多拉", "暴风龙"],
             "kind": "entity", "seq_from": 1, "seq_to": 3, "priority": 30,
             "content": "维鲁多拉（身份：暴风龙）。"},
        ],
    }

    (tmp_path / "data" / "story_analyses").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "timelines").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "lorebooks").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "story_analyses" / "S.json").write_text(
        json.dumps(analysis, ensure_ascii=False), encoding="utf-8"
    )
    (tmp_path / "data" / "timelines" / "S.json").write_text(
        json.dumps(timeline, ensure_ascii=False), encoding="utf-8"
    )
    (tmp_path / "data" / "lorebooks" / "S.json").write_text(
        json.dumps(lorebook, ensure_ascii=False), encoding="utf-8"
    )

    # 重定向 domain load_* 的目录常量到临时目录
    monkeypatch.setattr(
        "src.domain.novel.story_analysis.config._ANALYSIS_DIR", tmp_path / "data" / "story_analyses"
    )
    monkeypatch.setattr(
        "src.domain.novel.story_analysis.timeline._TIMELINE_DIR", tmp_path / "data" / "timelines"
    )
    monkeypatch.setattr(
        "src.domain.novel.story_analysis.lorebook._LOREBOOK_DIR", tmp_path / "data" / "lorebooks"
    )
    return tmp_path


def test_build_and_query_relations(world_kb):
    """懒构建 + relations 精确过滤。"""
    rows = svc.query_relations("S", entity="利姆露", entity2="维鲁多拉")
    assert len(rows) == 1
    assert rows[0]["relation_type"] == "伙伴"
    assert rows[0]["evidence_ids"] and "S__vol01_c002_n0000__s000" in rows[0]["evidence_ids"]


def test_query_events_by_era(world_kb):
    """events 按 era 过滤。"""
    rows = svc.query_events("S", era="转生后")
    assert len(rows) == 1
    assert rows[0]["summary"] == "利姆露苏醒。"


def test_query_timeline_and_character_events(world_kb):
    """timeline + by_character seq。"""
    tl = svc.query_timeline("S")
    assert len(tl) == 1 and tl[0]["seq"] == 1
    ce = svc.query_character_events("S", character="利姆露")
    assert len(ce) == 1 and json.loads(ce[0]["seqs"]) == [1]


def test_query_lorebook(world_kb):
    """lorebook 按 entity 命中（含 keys 别名）。"""
    rows = svc.query_lorebook("S", entity="暴风龙")
    assert len(rows) == 1
    assert rows[0]["entity"] == "维鲁多拉"


def test_missing_series_returns_empty(world_kb):
    """无源数据系列 → 空结果不报错。"""
    assert svc.query_relations("不存在系列") == []
    assert svc.query_events("不存在系列") == []


def test_tool_execute_relations(world_kb):
    """工具执行：relations 查询格式化输出。"""
    tool = WorldKnowledgeTool()
    import asyncio

    result = asyncio.run(tool.execute(
        query_type="relations", series_id="S", entity="利姆露", entity2="维鲁多拉"
    ))
    assert result.success
    assert "伙伴" in result.output
    assert "证据" in result.output


def test_tool_execute_missing_series(world_kb):
    """工具执行：无数据系列 → 成功但提示无数据。"""
    tool = WorldKnowledgeTool()
    import asyncio

    result = asyncio.run(tool.execute(query_type="timeline", series_id="不存在系列"))
    assert result.success
    assert "无匹配" in result.output
