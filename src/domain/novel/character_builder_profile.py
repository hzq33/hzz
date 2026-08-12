"""CharacterBuilder profile mixin — personality/speech conversion helpers.

Extracted from the former monolithic ``character_builder.py``; logic unchanged.
Mixin methods share instance state (``self._store`` / ``self.character`` etc.).
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from typing import Any

from src.domain.novel.models import PersonalityProfile, SpeechStyle

logger = logging.getLogger("agent")


_PERSONALITY_KEYWORDS: dict[str, list[str]] = {
    "清冷": ["清冷", "淡漠", "淡然", "疏离", "冷若冰霜", "不动声色", "冷声"],
    "温柔": ["温柔", "柔和", "体贴", "轻声", "含笑", "温和"],
    "活泼": ["笑", "跳", "跑", "热闹", "叽叽喳喳", "活泼", "开朗"],
    "坚毅": ["咬牙", "握紧", "坚定", "绝不", "一定", "毅然"],
    "沉默": ["沉默", "不说", "默然", "安静", "不语", "沉默片刻"],
    "聪慧": ["聪", "慧", "敏锐", "察觉", "发现", "看出"],
    "倔强": ["倔", "固执", "偏要", "我不要", "冷哼"],
    "豪爽": ["豪爽", "大笑", "痛快", "直率", "爽朗"],
    "阴险": ["阴险", "冷笑", "算计", "阴谋", "暗算"],
    "忠诚": ["忠诚", "誓死", "保护", "守护", "绝不背叛"],
}

_SPEECH_STYLE_KEYWORDS: dict[str, list[str]] = {
    "简洁": ["嗯", "哦", "是吗", "罢了", "好"],
    "古风": ["在下", "公子", "姑娘", "令尊", "家师", "晚辈", "便是"],
    "毒舌": ["蠢", "白痴", "无聊", "你以为", "油嘴滑舌"],
    "热血": ["我一定要", "绝不", "拼了", "放肆", "大胆"],
    "反问": ["难道", "岂", "何尝", "谁说"],
    "委婉": ["恐怕", "或许", "可能", "似乎", "大概"],
}


class CharacterBuilderProfileMixin:
    """Personality / speech-style profile conversion methods."""


    def _dict_to_personality_profile(self, data: dict, name: str) -> PersonalityProfile:
        """Convert LLM JSON dict to PersonalityProfile, with validation."""
        if not data:
            return self._empty_profile()

        traits = {}
        trait_dims = PersonalityProfile.TRAIT_DIMS
        raw_traits = data.get("traits", {})
        for dim in trait_dims:
            val = raw_traits.get(dim, 0.5)
            if isinstance(val, (int, float)):
                traits[dim] = round(max(0.0, min(1.0, float(val))), 2)
            else:
                traits[dim] = 0.5

        speech_data = data.get("speech", {})
        speech = SpeechStyle(
            vocabulary=str(speech_data.get("vocabulary", "")),
            sentence_pattern=str(speech_data.get("sentence_pattern", "")),
            catchphrase=str(speech_data.get("catchphrase", "")),
            emotional_expression=str(speech_data.get("emotional_expression", "")),
            rhythm=str(speech_data.get("rhythm", "")),
        )

        catchphrases = [
            str(p) for p in data.get("catchphrases", [])
            if isinstance(p, str) and 2 <= len(p) <= 20
        ][:5]

        emotional_tendencies = str(data.get("emotional_tendencies", ""))

        return PersonalityProfile(
            traits=traits,
            speech_patterns=speech,
            catchphrases=catchphrases,
            emotional_tendencies=emotional_tendencies,
        )

    def _fallback_personality_profile(
        self,
        name: str,
        narrative_snippets: list[str],
        dialogue_contents: list,
    ) -> PersonalityProfile:
        """Keyword-based fallback producing a structured PersonalityProfile.

        Converts the old keyword-match approach into the v2.0 structured format
        so downstream consumers (CharacterCard, etc.) always get a consistent type.
        """
        text = "。".join(narrative_snippets) if narrative_snippets else ""

        # 6-dim traits from keywords (rough heuristic)
        trait_keywords = {
            "extraversion": ["笑", "热闹", "活泼", "开朗", "大方", "主动"],
            "agreeableness": ["温柔", "体贴", "帮助", "关心", "劝", "安慰"],
            "conscientiousness": ["认真", "负责", "提前", "准备", "检查"],
            "neuroticism_reverse": ["冷静", "淡然", "不动声色", "镇定"],
            "dominance": ["命令", "要求", "决断", "做主", "统领", "威严"],
            "complexity": ["矛盾", "犹豫", "权衡", "复杂", "深思"],
        }
        traits = {}
        for dim, keywords in trait_keywords.items():
            score = sum(1.0 for kw in keywords if kw in text) / max(len(keywords), 1)
            traits[dim] = round(min(score * 1.5, 1.0), 2)

        # SpeechStyle from dialogue contents
        contents = []
        moods = []
        for item in (dialogue_contents or []):
            if isinstance(item, tuple):
                if len(item) >= 2:
                    contents.append(str(item[1]))
                if len(item) >= 3 and item[2]:
                    moods.append(item[2])
            elif isinstance(item, str):
                contents.append(item)

        all_text = " ".join(contents)
        avg_len = sum(len(c) for c in contents) / max(len(contents), 1) if contents else 0

        speech = SpeechStyle(
            vocabulary="",
            sentence_pattern=(
                f"平均句长{avg_len:.0f}字" +
                ("，短句为主" if avg_len < 12 else "，表达完整")
            ),
            catchphrase="",
            emotional_expression="",
            rhythm="",
        )

        # catchphrases: high-frequency short phrases (≥2 occurrences)
        short_phrases = [c for c in contents if 5 <= len(c) <= 20]
        catchphrases = [p for p, cnt in Counter(short_phrases).most_common(3) if cnt >= 2]

        # Emotional tendencies from mood tags
        emotional_tendencies = ""
        if moods:
            top_moods = Counter(moods).most_common(2)
            emotional_tendencies = "；".join(f"{m}({c}次)" for m, c in top_moods)

        return PersonalityProfile(
            traits=traits,
            speech_patterns=speech,
            catchphrases=catchphrases,
            emotional_tendencies=emotional_tendencies or "未提取",
        )

    @staticmethod
    def _empty_profile() -> PersonalityProfile:
        """Return an empty PersonalityProfile with neutral traits."""
        return PersonalityProfile(
            traits=dict.fromkeys(PersonalityProfile.TRAIT_DIMS, 0.5),
            speech_patterns=SpeechStyle(),
            catchphrases=[],
            emotional_tendencies="未提取",
        )

    @staticmethod
    def _profile_to_legacy_personality(profile: PersonalityProfile) -> str:
        """Convert PersonalityProfile.traits to legacy text string."""
        if not profile or not profile.traits:
            return ""
        trait_labels = {
            "extraversion": "外向", "agreeableness": "友善",
            "conscientiousness": "尽责", "neuroticism_reverse": "冷静",
            "dominance": "强势", "complexity": "复杂",
        }
        parts = []
        for dim, score in sorted(profile.traits.items(), key=lambda x: -x[1]):
            if score >= 0.6:
                label = trait_labels.get(dim, dim)
                parts.append(f"{label}({score:.0%})")
        return "、".join(parts[:4]) if parts else ""

    @staticmethod
    def _profile_to_legacy_speech_style(profile: PersonalityProfile) -> str:
        """Convert PersonalityProfile.speech_patterns to legacy text string."""
        if not profile or not profile.speech_patterns:
            return ""
        sp = profile.speech_patterns
        parts = []
        if sp.sentence_pattern:
            parts.append(sp.sentence_pattern[:60])
        if sp.vocabulary:
            parts.append(sp.vocabulary[:40])
        if sp.rhythm:
            parts.append(sp.rhythm[:40])
        return "；".join(parts) if parts else ""

    # ── Legacy extraction methods (kept for fallback compat) ─

    def _extract_personality(self, snippets: list[str]) -> str:
        """[LEGACY] Extract personality traits from narrative snippets (keyword)."""
        if not snippets:
            return ""

        text = "。".join(snippets)
        traits = []
        for trait, keywords in _PERSONALITY_KEYWORDS.items():
            if any(kw in text for kw in keywords):
                traits.append(trait)
        return "、".join(traits) if traits else ""

    def _extract_speech_style(self, dialogue_contents: list) -> str:
        """[LEGACY] Extract speech style from dialogue contents (keyword)."""
        if not dialogue_contents:
            return ""

        contents = [c for _, c, _ in dialogue_contents]
        all_text = " ".join(contents)
        avg_len = sum(len(c) for c in contents) / len(contents)

        styles = []

        # Length-based
        if avg_len < 10:
            styles.append("简洁短促")
        elif avg_len > 30:
            styles.append("表达完整")

        # Keyword-based
        for style, keywords in _SPEECH_STYLE_KEYWORDS.items():
            if any(kw in all_text for kw in keywords):
                styles.append(style)

        # Punctuation-based
        if len(contents) > 0:
            q_ratio = all_text.count("？") / len(contents)
            if q_ratio > 0.4:
                styles.append("善用反问")
            e_ratio = all_text.count("！") / len(contents)
            if e_ratio > 0.3:
                styles.append("语气强烈")

        return "；".join(styles) if styles else ""

    def _extract_relationships(
        self, co_occurrence: Counter, character: str
    ) -> str:
        """Extract relationship clues from co-occurrence data."""
        if not co_occurrence:
            return ""

        # Top 3 most co-occurring characters
        relations = []
        for other_name, count in co_occurrence.most_common(5):
            if count >= 2:
                relations.append(f"{other_name}(共现{count}次)")
        return "、".join(relations[:3]) if relations else ""


