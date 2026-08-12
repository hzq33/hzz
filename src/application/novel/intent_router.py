"""Intent router — classify user query and select the best RAG channel.

Simple keyword + pattern matching. Can be upgraded to LLM-based
classification for better accuracy on ambiguous queries.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import ClassVar

from src.domain.novel.models import (
    BLOCK_CHARACTER,
    BLOCK_DIALOGUE,
    BLOCK_NARRATIVE,
    BLOCK_QA,
)


@dataclass
class IntentResult:
    """Classification result with channel routing info."""

    primary_channel: str
    channel_weights: dict[str, float]
    filters: dict  # Metadata filters for the search
    confidence: float  # 0.0–1.0

    is_imitation: bool = False
    is_global: bool = False  # GraphRAG 全局问答（主线/主题/整体关系网）
    target_characters: list[str] = field(default_factory=list)
    style_hint: str = ""


class IntentRouter:
    """Classify user query into routing strategies including character channel."""

    QA_QUESTION_WORDS: ClassVar[list[str]] = [
        "谁", "什么", "怎么", "为什么", "哪里", "什么时候",
        "如何", "为何", "哪", "多少", "多久", "怎样",
    ]

    QA_FACT_PATTERNS: ClassVar[list[re.Pattern]] = [
        re.compile(r"(.+)(是什么|是谁|在哪里|什么时候|怎么回事)"),
        re.compile(r"(.+)的(结局|真相|秘密|原因|来历|身份)"),
        re.compile(r"(怎么|如何)(.+)的"),
    ]

    IMITATE_PATTERNS: ClassVar[list[re.Pattern]] = [
        re.compile(r"(模仿|用|以|学)(.+)(语气|风格|口吻|方式)"),
        re.compile(r"(扮演|假装|cos)(.+)"),
        re.compile(r"(写|生成|创作)(一段|一个)(.+)对话"),
        re.compile(r"(.+)(会怎么说|会怎么想|会怎么做)"),
    ]

    GLOBAL_PATTERNS: ClassVar[list[re.Pattern]] = [
        # 全书/整体类综述
        re.compile(r"(这本小说|这本书|这部小说|这部作品|整部书|全书|作品整体|整个故事).*(主线|主题|讲了|说什么|梗概|概要|概述|脉络|结构|大纲|剧情|故事)"),
        re.compile(r"(主线|主题|梗概|概要|脉络|世界观|设定|故事线).*(是什么|如何|怎样|讲讲|介绍一下)"),
        re.compile(r"(这本书|整部书|这部作品|整个故事).*(讲了|关于|围绕|结局|世界观)"),
        re.compile(r"(主要角色|核心人物).*(群|有哪些|是谁|关系|介绍)"),
        re.compile(r"(.+)(人物关系网|关系网|整体关系|全局).*(如何|怎样|是什么)"),
        re.compile(r"(概括|总结|概述|简述).*(小说|剧情|故事|内容|主线|情节)"),
    ]

    NARRATIVE_PATTERNS: ClassVar[list[re.Pattern]] = [
        re.compile(r"(描写|描述|刻画)(.+)"),
        re.compile(r"(.+)的(场景|环境|氛围|样子)"),
        re.compile(r"(原文|原著|书中).*(段落|片段|章节|后记)?"),
        re.compile(r"(后记|序章|序言|前言|跋)"),
        re.compile(r"(完整|全部|整段).*(原文|内容|后记)"),
    ]

    CHARACTER_PATTERNS: ClassVar[list[re.Pattern]] = [
        re.compile(r"(.+)的(性格|人设|简介|介绍|画像|人品)"),
        re.compile(r"(介绍|说说|讲讲)一下(.+)"),
        re.compile(r"(.+)是一个怎样的(人|角色)"),
        re.compile(r"(角色|人物)(.+)(资料|设定|档案)"),
    ]

    RELATION_PATTERNS: ClassVar[list[re.Pattern]] = [
        re.compile(r"(.+)(和|与|跟)(.+)(关系|认识|朋友|敌人|恋人|相处|对峙|敌对|结盟)"),
        re.compile(r"(.+)的(关系|朋友|敌人|爱人|亲人|态度)"),
        re.compile(r"(后来|怎么样了|怎样了).*(关系|相处|敌对|结盟)"),
        re.compile(r"(关系|相处|敌对|结盟|冲突|和好).*(怎么样|如何|怎样)"),
        re.compile(r"(发生了什么|关键事件|伏笔|里程碑)"),
    ]

    _WEIGHTS: ClassVar[dict[str, dict[str, float]]] = {
        "qa":        {BLOCK_QA: 1.0},
        "imitate":   {BLOCK_DIALOGUE: 1.0},
        "narrative": {BLOCK_NARRATIVE: 1.0},
        "character": {BLOCK_CHARACTER: 1.0},
        "mixed": {
            # qa 数据未提取，暂时从混合权重中移除
            # BLOCK_QA: 0.35,
            # dialogue 移出事实混合检索（风格/模仿由独立风格检索承担）
            BLOCK_NARRATIVE: 0.75,
            BLOCK_CHARACTER: 0.25,
        },
    }

    _KNOWN_CHARACTERS: ClassVar[list[str]] = [
        "苏瑶", "顾辰", "苏玥", "林晚", "沈墨",
    ]

    def __init__(self, mixed_weights: dict[str, float] | None = None):
        self._weights: dict[str, dict[str, float]] = {
            key: dict(val) for key, val in self._WEIGHTS.items()
        }
        if mixed_weights:
            cleaned = {
                str(k): float(v)
                for k, v in mixed_weights.items()
                if k in (
                    BLOCK_QA,
                    BLOCK_DIALOGUE,
                    BLOCK_NARRATIVE,
                    BLOCK_CHARACTER,
                )
                and float(v) > 0
            }
            if cleaned:
                self._weights["mixed"] = cleaned

    def classify(self, query: str, available_characters: list[str] = None) -> IntentResult:
        chars = available_characters or self._KNOWN_CHARACTERS

        # GraphRAG 全局问答（主线/主题/整体关系网）优先于碎片检索
        for pattern in self.GLOBAL_PATTERNS:
            if pattern.search(query):
                return IntentResult(
                    primary_channel=BLOCK_NARRATIVE,
                    channel_weights=self._weights["mixed"],
                    filters={},
                    confidence=0.75,
                    is_global=True,
                )

        for pattern in self.IMITATE_PATTERNS:
            if pattern.search(query):
                target = self._extract_characters(query, chars)
                return IntentResult(
                    primary_channel=BLOCK_DIALOGUE,
                    channel_weights=self._weights["imitate"],
                    filters={"characters": list(target)} if target else {},
                    confidence=0.85,
                    is_imitation=True,
                    target_characters=target,
                    style_hint=self._extract_style(query),
                )

        # Relation/event before persona keywords (channel=character is relation/event index).
        for pattern in self.RELATION_PATTERNS:
            if pattern.search(query):
                target = self._extract_characters(query, chars)
                return IntentResult(
                    primary_channel=BLOCK_CHARACTER,
                    channel_weights={
                        BLOCK_CHARACTER: 0.6,
                        BLOCK_NARRATIVE: 0.4,
                    },
                    filters={"characters": list(target)} if target else {},
                    confidence=0.8,
                    target_characters=target,
                )

        for pattern in self.CHARACTER_PATTERNS:
            if pattern.search(query):
                target = self._extract_characters(query, chars)
                # Persona/traits live on CharacterCard; retrieve from narrative/dialogue.
                return IntentResult(
                    primary_channel=BLOCK_NARRATIVE,
                    channel_weights={
                        BLOCK_NARRATIVE: 0.65,
                        BLOCK_CHARACTER: 0.35,
                    },
                    filters={"characters": list(target)} if target else {},
                    confidence=0.82,
                    target_characters=target,
                )

        for pattern in self.NARRATIVE_PATTERNS:
            if pattern.search(query):
                return IntentResult(
                    primary_channel=BLOCK_NARRATIVE,
                    channel_weights=self._weights["narrative"],
                    filters={},
                    confidence=0.80,
                )

        if self._is_question(query):
            return IntentResult(
                primary_channel=BLOCK_NARRATIVE,
                channel_weights=self._weights["mixed"],
                filters={},
                confidence=0.55,
            )

        return IntentResult(
            primary_channel=BLOCK_NARRATIVE,
            channel_weights=self._weights["mixed"],
            filters={},
            confidence=0.3,
        )

    def _is_question(self, query: str) -> bool:
        if any(query.endswith(qw) for qw in ["？", "?"]):
            return True
        if any(qw in query for qw in self.QA_QUESTION_WORDS):
            return True
        return any(p.search(query) for p in self.QA_FACT_PATTERNS)

    async def aclassify(
        self,
        query: str,
        available_characters: list[str] = None,
        query_context=None,
    ) -> IntentResult:
        """Async wrapper — regex routing is CPU-only, no awaits needed.

        Lets async callers (e.g. NovelRetrieval.search_raw) use a uniform
        ``aclassify`` interface across IntentRouter / LLMIntentRouter.
        """
        return self.classify(query, available_characters)

    @staticmethod
    def _extract_characters(query: str, known: list[str]) -> list[str]:
        found = []
        for name in known:
            if name in query:
                found.append(name)
        return found

    @staticmethod
    def _extract_style(query: str) -> str:
        styles = ["清冷", "温柔", "毒舌", "热血", "寡言", "古风", "少年意气"]
        for s in styles:
            if s in query:
                return s
        return ""
