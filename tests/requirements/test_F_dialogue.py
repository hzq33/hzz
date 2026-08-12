"""需求域 F — 对话抽取与归因：按章抽取 / 章过滤 / 配额 / 置信度 / 噪声 / 降级。

覆盖 docs/REQUIREMENTS.md F-01 ~ F-06。
黑盒：走公开函数（extract_window / plan_document_windows / should_skip_chapter /
build_quota_tracker / is_noise_speaker / extract_dialogue_for_document）。
"""

from __future__ import annotations

import asyncio

from src.domain.novel.dialogue_chunk import (
    plan_document_windows,
    should_skip_chapter,
)
from src.domain.novel.dialogue_llm import LLMDialogueExtractor
from src.domain.novel.dialogue_quota import build_quota_tracker
from tests.conftest import FakeLLM, make_store


def _chapter(title: str, text: str):
    from src.domain.novel.models import Chapter

    return Chapter(title=title, text=text)


# ── F-01 按章 LLM 抽取 ────────────────────────────────────────


async def test_extract_window_parses_turns():
    """F-01：单窗 LLM 抽取同时产出说话人与台词（含置信度）。"""
    llm = FakeLLM(
        payload='{"dialogues": [{"speaker": "利姆露", "content": "你好", "confidence": 0.95},'
        ' {"speaker": "维鲁多拉", "content": "醒了？", "confidence": 0.88}]}'
    )
    extractor = LLMDialogueExtractor(llm)
    turns = await extractor.extract_window(
        "「你好。」利姆露说道。「醒了？」维鲁多拉回答。",
        chapter_title="第一章",
        candidates=["利姆露", "维鲁多拉"],
    )
    assert len(turns) == 2
    speakers = {t["speaker"] for t in turns}
    assert {"利姆露", "维鲁多拉"} <= speakers
    assert all(t.get("confidence") is not None for t in turns)


async def test_extract_window_bare_list_json():
    """F-01：LLM 输出裸数组 JSON 也能解析。"""
    llm = FakeLLM(payload='[{"speaker": "烧盐", "content": "开饭了"}]')
    extractor = LLMDialogueExtractor(llm)
    turns = await extractor.extract_window("「开饭了」烧盐大声说道，招呼大家赶快过来吃饭。")
    assert turns and turns[0]["speaker"] == "烧盐"


async def test_extract_window_unparsable_returns_empty():
    """F-06：LLM 输出不可解析 → 返回空列表（调用方降级，不崩）。"""
    llm = FakeLLM(payload="这不是 JSON 完全没有结构")
    extractor = LLMDialogueExtractor(llm)
    turns = await extractor.extract_window("「你好。」利姆露说道，语气十分温和。")
    assert turns == []


async def test_extract_window_llm_error_returns_empty():
    """F-06：LLM 调用异常 → 返回空列表（不 500）。"""
    llm = FakeLLM(exc=RuntimeError("boom"))
    extractor = LLMDialogueExtractor(llm)
    turns = await extractor.extract_window("「你好。」利姆露说道，语气十分温和。")
    assert turns == []


# ── F-02 章过滤 ───────────────────────────────────────────────


def test_skip_no_quote_chapter():
    """F-02：无引号章被跳过（require_quote_marks）。"""
    reason = should_skip_chapter(
        "第一章",
        "这是一段没有任何对话引号的纯叙事文字。",
        require_quote_marks=True,
        min_chapter_chars=5,
    )
    assert reason


def test_keep_quote_chapter():
    """F-02：有引号章不跳过。"""
    reason = should_skip_chapter(
        "第一章",
        "「你好。」利姆露说道。",
        require_quote_marks=True,
        min_chapter_chars=5,
    )
    assert not reason


def test_skip_short_chapter():
    """F-02：过短且无引号章（min_chapter_chars）被跳过。"""
    reason = should_skip_chapter(
        "短章",
        "短",
        require_quote_marks=True,
        min_chapter_chars=80,
    )
    assert reason


def test_skip_metadata_title():
    """F-02：简介/后记/制作信息标题被跳过。"""
    for title in ("简介", "后记", "制作信息", "afterword", "译后记"):
        reason = should_skip_chapter(
            title,
            "「有引号但标题是元信息」",
            require_quote_marks=True,
            min_chapter_chars=5,
        )
        assert reason, f"标题 {title} 应被跳过"


def test_plan_document_windows_filters_and_slides():
    """F-01/F-02：整文档窗口规划——无引号章跳过、超长章滑窗。"""
    chapters = [
        _chapter("第一章", "「你好。」利姆露说道。" * 200),  # 超长 → 多窗
        _chapter("后记", "无引号的制作信息"),  # 标题黑名单
        _chapter("第二章", "纯叙事没有引号。"),  # 无引号
        _chapter("第三章", "「开饭了。」烧盐说。"),  # 正常
    ]
    windows, skipped, meta = plan_document_windows(
        chapters,
        max_chunk_chars=300,
        slide_win_chars=200,
        slide_stride_chars=100,
        require_quote_marks=True,
        min_chapter_chars=5,
    )
    assert windows, "应有抽取窗口"
    assert meta["chapters_total"] == 4
    assert any(s.reason for s in skipped), "应有跳过记录"
    assert meta.get("slide_chapters", 0) >= 1, "超长章应触发滑窗"


# ── F-03 配额分层 ─────────────────────────────────────────────


def test_quota_targets_by_importance():
    """F-03：配额按 importance 档位设定（main 高、supporting 中、extra 低）。"""
    tracker = build_quota_tracker(
        [
            {"name": "利姆露", "mention_count": 200, "importance": "main"},
            {"name": "朱菜", "mention_count": 50, "importance": "supporting"},
            {"name": "路人", "mention_count": 10, "importance": "extra"},
        ],
        quotas={"main": 50, "supporting": 40, "extra": 10},
        main_top_n=5,
        supporting_top_n=20,
        promote_importance_by_mentions=False,
    )
    assert tracker.target_of("利姆露") == 50
    assert tracker.target_of("朱菜") == 40
    assert tracker.target_of("路人") == 10


def test_quota_record_and_remaining():
    """F-03：record 累计计数，remaining 反映剩余配额。"""
    tracker = build_quota_tracker(
        [{"name": "利姆露", "mention_count": 100, "importance": "main"}],
        quotas={"main": 5, "supporting": 3, "extra": 1},
        promote_importance_by_mentions=False,
    )
    assert tracker.remaining("利姆露") == 5
    tracker.record("利姆露", chapter_index=0)
    tracker.record("利姆露", chapter_index=0)
    tracker.record("利姆露", chapter_index=1)
    assert tracker.remaining("利姆露") == 2
    assert tracker.counts["利姆露"] == 3


def test_quota_priority_satisfied_when_main_covered():
    """F-03：main 角色配额与章节覆盖达标 → 提前停止后续抽取。"""
    tracker = build_quota_tracker(
        [{"name": "利姆露", "mention_count": 100, "importance": "main"}],
        quotas={"main": 2, "supporting": 10, "extra": 1},
        main_top_n=5,
        supporting_top_n=20,
        promote_importance_by_mentions=False,
    )
    # main 角色 2 次、覆盖 2 章 → 达标
    tracker.record("利姆露", chapter_index=0)
    tracker.record("利姆露", chapter_index=1)
    assert tracker.priority_satisfied(dialogue_chapter_count=2) is True


def test_quota_priority_not_satisfied_when_short():
    """F-03：main 角色未达标 → 不提前停止（继续抽取）。"""
    tracker = build_quota_tracker(
        [{"name": "利姆露", "mention_count": 100, "importance": "main"}],
        quotas={"main": 5, "supporting": 10, "extra": 1},
        promote_importance_by_mentions=False,
    )
    tracker.record("利姆露", chapter_index=0)
    assert tracker.priority_satisfied(dialogue_chapter_count=5) is False


def test_quota_blacklist_not_main():
    """F-03/F-05：importance 黑名单（物种词）不参与 main 档位。"""
    tracker = build_quota_tracker(
        [{"name": "史莱姆", "mention_count": 999, "importance": "main"}],
        quotas={"main": 50, "supporting": 40, "extra": 10},
        importance_blacklist=["史莱姆", "哥布林"],
        main_top_n=5,
        supporting_top_n=20,
    )
    assert tracker.importance_of("史莱姆") != "main"


# ── F-04 说话人归因 ───────────────────────────────────────────


def test_quota_resolve_alias_to_canonical():
    """F-04：别名/称呼归一到 canonical（候选名归并）。"""
    tracker = build_quota_tracker(
        [
            {
                "name": "利姆露",
                "aliases": ["利姆路", "头目"],
                "mention_count": 100,
                "importance": "main",
            }
        ],
        promote_importance_by_mentions=False,
    )
    assert tracker.resolve("利姆路") == "利姆露"
    assert tracker.resolve("头目") == "利姆露"


async def test_extract_turn_confidence_preserved():
    """F-04：抽取保留置信度（供高置信/接受阈值判定）。"""
    llm = FakeLLM(
        payload='{"dialogues": [{"speaker": "利姆露", "content": "你好", "confidence": 0.99}]}'
    )
    extractor = LLMDialogueExtractor(llm)
    turns = await extractor.extract_window(
        "「你好。」利姆露微笑着说道，语气十分温和。"
    )
    assert turns[0]["confidence"] == 0.99


# ── F-05 噪声与黑名单 ─────────────────────────────────────────


def test_is_noise_speaker():
    """F-05：噪声说话人（代词/碎片）被识别。"""
    from src.domain.novel.dialogue_span import is_noise_speaker

    assert is_noise_speaker("他说") is True
    assert is_noise_speaker("他") is True  # 代词集合
    assert is_noise_speaker("某人") is True  # 代词引导碎片
    assert is_noise_speaker("利姆露") is False


def test_noise_name_policy():
    """F-05：职业/身份词按策略识别为噪声名。"""
    from src.domain.novel.character_policy import is_noise_name

    assert is_noise_name("图书管理员") is True
    assert is_noise_name("店员") is True
    assert is_noise_name("利姆露") is False


# ── F-06 降级路径 ─────────────────────────────────────────────


async def test_provider_off_no_llm_calls():
    """F-06：provider=off 时不调 LLM（规则/降级路径），入库不失败。"""
    from src.domain.novel.models import NovelDocument

    from src.application.novel.dialogue_pipeline.extract import (
        extract_dialogue_for_document,
    )

    doc = NovelDocument(
        doc_id="S__vol01",
        chapters=[_chapter("第一章", "「你好。」利姆露说道。")],
    )
    llm = FakeLLM()
    result = await extract_dialogue_for_document(
        doc,
        "S__vol01",
        llm_client=llm,
        config={"provider": "off"},
    )
    assert llm.calls == 0, "off 模式不应调用 LLM"
    assert isinstance(result.blocks, list)


# ── F-03 补充：L3 按需补抽 ────────────────────────────────────


async def test_deepen_empty_store_graceful():
    """F-03：L3 补抽在无证据时优雅返回（不崩）。"""
    from src.application.novel.dialogue_pipeline.deepen import deepen_dialogue_from_store

    store = make_store()
    result = await deepen_dialogue_from_store(
        store,
        canonical_name="利姆露",
        llm_client=FakeLLM(),
    )
    assert isinstance(result.blocks, list)
