"""CharacterBuilder — build character-type NovelBlock records from dialogue/narrative blocks.

v2.0: Structured PersonalityProfile + SpeechStyle extraction via LLM (Phase 2).
Analyzes all dialogue and narrative blocks to extract character profiles,
producing one NovelBlock (block_type=character) per discovered character.
These blocks are indexed into the novel:character FAISS channel.
"""

from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict

from src.domain.name_resolver import NameResolver
from src.domain.novel.character_builder_profile import CharacterBuilderProfileMixin
from src.domain.novel.models import (
    BLOCK_CHARACTER,
    CharacterIdentity,
    NovelBlock,
    PersonalityProfile,
    SpeechStyle,
)

logger = logging.getLogger("agent")

# ── Heuristic keyword maps (fallback only, not primary) ─────



_MIN_APPEARANCES = 2

# Common non-name words that should be filtered out
_NON_NAME_WORDS = {
    # Adverbs/adjectives that look like names
    "终于", "随即", "忽然", "突然", "渐渐", "慢慢", "轻轻", "微微",
    "冷冷", "认真", "相视", "过一", "一丝",
    # Common nouns/titles
    "姑娘", "公子", "少爷", "小姐", "师父", "师傅", "弟子",
    "父亲", "母亲", "前辈", "晚辈",
    "少年", "少女", "少年人", "中年人", "年轻人",
    # Location/time nouns
    "四周", "身边", "后面", "前面", "里面", "外面", "旁边",
    "时候", "不知", "两人", "一人", "三人", "几人", "数人",
    # Verbs that look like names
    "冷笑", "笑道", "点头", "摇头", "转身", "抬头", "低头",
    # Dialogue phrases / exclamations
    "胡说", "胡说八", "胡说八道",
    "两人齐声", "众人齐声", "齐声",
    "我听", "我问", "我看", "你听", "你说", "他就",
    "却被", "也被", "还将", "却又",
    "剑尖", "那人一", "那人",
    # Other
    "此人", "众人", "大家", "有人",
    # Common false positives from narrative pattern matching
    "过一丝", "清寒也", "相视而", "心中急", "心中焦",
    "也感到", "也拔剑", "也停下", "也停下脚步",
    # Compound noise from name+verb粘连 that slipped through cleaning
    "林晚晴冷", "顾清寒冷", "林震天笑", "林震天大",
    "姑娘不", "不过师父", "我就知", "你知",
}


# ── LLM personality extraction prompt (Phase 2) ────────────

_PERSONALITY_EXTRACTION_PROMPT = """你是小说角色分析专家。根据提供的叙事片段和对话内容，
分析角色的性格特征和说话风格。输出严格 JSON（不要任何其他文字）：

{
  "traits": {
    "extraversion": 0.0,
    "agreeableness": 0.0,
    "conscientiousness": 0.0,
    "neuroticism_reverse": 0.0,
    "dominance": 0.0,
    "complexity": 0.0
  },
  "speech": {
    "vocabulary": "常用词汇特征描述",
    "sentence_pattern": "句式特点描述",
    "catchphrase": "口头禅固定表达",
    "emotional_expression": "情绪表达方式描述",
    "rhythm": "语言节奏描述"
  },
  "catchphrases": ["口头禅1"],
  "emotional_tendencies": "情绪表达倾向描述（1-2句）"
}

规则：
1. 每个 trait 给 0.0-1.0 的分数，区分度高（不要全给中间值）
2. speech 维度用自然语言描述，每项 1-2 句即可
3. catchphrases 从对话中提取真正重复出现的短语（≤15字），不要编造
4. 只输出 JSON，不要任何其他文字
"""





def _parse_llm_json(raw: str) -> dict:
    """Robust JSON extraction from LLM output (3 strategies)."""
    import json as _json
    import re as _re
    if not raw or not raw.strip():
        return {}
    # Strategy 1: direct
    try:
        return _json.loads(raw)
    except _json.JSONDecodeError:
        pass
    # Strategy 2: code fence
    m = _re.search(r'```(?:json)?\s*\n?(.*?)\n?```', raw, _re.DOTALL)
    if m:
        try:
            return _json.loads(m.group(1))
        except _json.JSONDecodeError:
            pass
    # Strategy 3: first { ... }
    start = raw.find('{')
    end = raw.rfind('}')
    if start != -1 and end > start:
        try:
            return _json.loads(raw[start:end + 1])
        except _json.JSONDecodeError:
            pass
    return {}





class CharacterBuilder(CharacterBuilderProfileMixin):
    """Build character-type NovelBlock records from dialogue/narrative blocks.

    v2.0: Structured PersonalityProfile extraction via LLM (DeepSeek API).
    Falls back to keyword-based extraction when LLM is unavailable.

    Usage:
        builder = CharacterBuilder()
        char_blocks = builder.build_all(narrative_blocks, dialogue_blocks,
                                         doc_id="镜湖风云录", llm_client=shared_llm)
        # char_blocks: List[NovelBlock] with block_type=character
        # Each block has character_identity (v2.0) and personality_profile (v2.0)
    """

    async def build_all(
        self,
        narrative_blocks: list[NovelBlock],
        dialogue_blocks: list[NovelBlock],
        doc_id: str = "",
        llm_extractor=None,   # Optional: LocalLLMCharacterExtractor for semantic personality
        llm_client=None,      # Phase 2: SharedLLMClient for structured PersonalityProfile extraction
    ) -> list[NovelBlock]:
        """Build character blocks from all narrative and dialogue blocks.

        Steps:
        1. Discover characters from dialogue blocks (speakers)
        2. Resolve aliases via NameResolver (四阶段别名消解 + 全名补充)
        3. Merge stats under canonical names
        4. Filter to significant characters
        5. Extract structured PersonalityProfile (LLM) or fallback (keyword)
        6. Build NovelBlock (block_type=character) for each

        Args:
            narrative_blocks: All narrative-type blocks.
            dialogue_blocks: All dialogue-type blocks.
            doc_id: Document identifier.
            llm_client: SharedLLMClient for structured personality extraction.

        Returns:
            List of character-type NovelBlock records with CharacterIdentity
            and PersonalityProfile.
        """
        # Step 1: Discover all characters and their stats
        char_stats = self._discover_characters(narrative_blocks, dialogue_blocks)

        # Step 2: Resolve aliases via NameResolver
        resolver = NameResolver()
        raw_names = list(char_stats.keys())
        co_occurrence = {
            name: stats["co_occurrence"] for name, stats in char_stats.items()
        }
        identities = resolver.resolve(raw_names, co_occurrence)

        # Step 3: Merge stats under canonical names
        lookup = resolver.create_lookup_map(identities)
        merged_stats: dict[str, dict] = {}
        for raw_name, stats in char_stats.items():
            canonical = lookup.get(raw_name, raw_name)
            if canonical not in merged_stats:
                merged_stats[canonical] = {
                    "appearance_count": 0,
                    "dialogue_count": 0,
                    "dialogue_contents": [],
                    "narrative_snippets": [],
                    "co_occurrence": Counter(),
                    "style_tags": Counter(),
                    "mood_tags": Counter(),
                }
            ms = merged_stats[canonical]
            ms["appearance_count"] += stats["appearance_count"]
            ms["dialogue_count"] += stats["dialogue_count"]
            ms["dialogue_contents"].extend(stats["dialogue_contents"])
            ms["narrative_snippets"].extend(
                s for s in stats["narrative_snippets"] if s not in ms["narrative_snippets"]
            )
            # Merge co-occurrence: remap names to canonical
            for other_name, count in stats["co_occurrence"].items():
                other_canonical = lookup.get(other_name, other_name)
                if other_canonical != canonical:  # Don't self-reference
                    ms["co_occurrence"][other_canonical] += count
            for tag, count in stats["style_tags"].items():
                ms["style_tags"][tag] += count
            for mood, count in stats["mood_tags"].items():
                ms["mood_tags"][mood] += count

        # Cap narrative snippets to prevent memory bloat
        for ms in merged_stats.values():
            ms["narrative_snippets"] = ms["narrative_snippets"][:10]
            ms["dialogue_contents"] = ms["dialogue_contents"][:20]

        # Step 4: Filter to significant characters
        significant = {
            name: stats
            for name, stats in merged_stats.items()
            if (stats["appearance_count"] >= _MIN_APPEARANCES
                or stats["dialogue_count"] >= 2)
            and name not in _NON_NAME_WORDS
            and len(name) >= 2  # Skip single-char names
            and name not in ("俺", "僕", "私", "あたし", "わたし", "彼女", "あいつ")
            and not name.endswith("地")
            and not name.endswith("的")
        }

        # LLM cleanup: when >8 candidates, use LLM to filter non-characters
        if len(significant) > 8:
            try:
                significant = await _llm_filter_characters(significant)
            except Exception:
                pass  # LLM unavailable → keep all

        if not significant:
            logger.info("No significant characters found in %s", doc_id)
            return []

        logger.info(
            "Found %d characters in %s (after alias resolution): %s",
            len(significant), doc_id, list(significant.keys()),
        )

        # Step 5: Build a NovelBlock per character
        char_blocks: list[NovelBlock] = []
        for canonical_name, stats in significant.items():
            identity = identities.get(canonical_name)
            block = await self._build_character_block(
                name=canonical_name,
                stats=stats,
                narrative_blocks=narrative_blocks,
                dialogue_blocks=dialogue_blocks,
                doc_id=doc_id,
                llm_extractor=llm_extractor,
                llm_client=llm_client,
                identity=identity,
            )
            char_blocks.append(block)

        return char_blocks

    def _discover_characters(
        self,
        narrative_blocks: list[NovelBlock],
        dialogue_blocks: list[NovelBlock],
    ) -> dict[str, dict]:
        """Discover characters and count their appearances.

        Two-phase approach:
        Phase 1: Extract speaker names from dialogue blocks (high confidence).
        Phase 2: Scan narrative blocks for known names.

        Returns:
            Dict mapping character name → stats dict.
        """
        char_stats: dict[str, dict] = defaultdict(lambda: {
            "appearance_count": 0,
            "dialogue_count": 0,
            "dialogue_contents": [],
            "narrative_snippets": [],
            "co_occurrence": Counter(),
            "style_tags": Counter(),
            "mood_tags": Counter(),
        })

        # Phase 1: Discover characters from dialogue speakers
        for db in dialogue_blocks:
            block_characters = set()

            for turn in db.dialogues:
                speaker = turn.speaker
                if not speaker or speaker == "未知":
                    continue

                block_characters.add(speaker)
                stats = char_stats[speaker]
                stats["dialogue_count"] += 1
                stats["appearance_count"] += 1
                stats["dialogue_contents"].append((speaker, turn.content, turn.mood))

                if turn.mood:
                    stats["mood_tags"][turn.mood] += 1

            # Co-occurrence in dialogue block
            for c1 in block_characters:
                for c2 in block_characters:
                    if c1 != c2:
                        char_stats[c1]["co_occurrence"][c2] += 1

            # Style tags from block
            for tag in db.style_tags:
                if tag != "未分类":
                    for c in block_characters:
                        char_stats[c]["style_tags"][tag] += 1

        # Phase 2: Track known characters in narrative
        for nb in narrative_blocks:
            text = nb.narrative_text
            if not text:
                continue
            for sentence in re.split(r"[。！？\n]", text):
                sentence = sentence.strip()
                if not sentence:
                    continue
                mentioned = []
                for name in list(char_stats.keys()):
                    if name in sentence:
                        if name not in mentioned:
                            mentioned.append(name)
                        char_stats[name]["appearance_count"] += 1
                        if len(char_stats[name]["narrative_snippets"]) < 5:
                            char_stats[name]["narrative_snippets"].append(sentence)
                for i, c1 in enumerate(mentioned):
                    for c2 in mentioned[i + 1:]:
                        char_stats[c1]["co_occurrence"][c2] += 1
                        char_stats[c2]["co_occurrence"][c1] += 1

        return dict(char_stats)

    async def _build_character_block(
        self,
        name: str,
        stats: dict,
        narrative_blocks: list[NovelBlock],
        dialogue_blocks: list[NovelBlock],
        doc_id: str,
        llm_extractor=None,
        llm_client=None,
        identity: CharacterIdentity | None = None,
    ) -> NovelBlock:
        """Build a single character-type NovelBlock.

        v2.0: Extracts structured PersonalityProfile via LLM (preferred)
        or keyword-based fallback.

        Args:
            name: Canonical character name (post-alias-resolution).
            stats: Merged stats dict.
            llm_client: SharedLLMClient for structured extraction.
            identity: CharacterIdentity from NameResolver.
        """
        snippets = stats.get("narrative_snippets", []) or []
        dialogue_data = stats.get("dialogue_contents") or []

        # Extract plain dialogue contents for LLM input
        contents_for_profile = [
            item[1] if (isinstance(item, tuple) and len(item) >= 2) else str(item)
            for item in dialogue_data
        ]

        # ── v2.0: Structured PersonalityProfile extraction ──
        personality_profile: PersonalityProfile | None = None

        if llm_client is not None:
            try:
                personality_profile = await self._extract_personality_profile(
                    name=name,
                    narrative_snippets=snippets,
                    dialogue_contents=contents_for_profile,
                    llm_client=llm_client,
                )
                logger.info(
                    "Extracted structured PersonalityProfile for '%s': traits=%s",
                    name, {k: round(v, 2) for k, v in (personality_profile.traits or {}).items()},
                )
            except Exception as e:
                logger.warning("LLM personality extraction failed for '%s': %s", name, e)

        if personality_profile is None:
            personality_profile = self._fallback_personality_profile(
                name=name,
                narrative_snippets=snippets,
                dialogue_contents=dialogue_data,
            )

        # ── Legacy text fields (backward compat) ──
        personality = self._profile_to_legacy_personality(personality_profile)
        speech_style = self._profile_to_legacy_speech_style(personality_profile)

        # Also try LocalLLMExtractor for semantic override if available
        if llm_extractor is not None and not personality_profile.traits:
            try:
                contents_legacy = [c for _, c, _ in dialogue_data]
                profile = await llm_extractor.extract_personality(name, snippets, contents_legacy)
                if profile.get("personality"):
                    personality = profile["personality"]
                if profile.get("speaking_style"):
                    speech_style = profile["speaking_style"]
            except Exception:
                pass

        # Background from first few narrative mentions
        background = "。".join(snippets[:3])[:300]

        # Relationships from co-occurrence
        relationships = self._extract_relationships(stats["co_occurrence"], name)

        # Sample dialogues (top 5 most representative)
        sample_dialogues = [
            content for _, content, _ in dialogue_data[:5]
        ]

        # Style tags
        style_tags = [
            tag for tag, _ in stats["style_tags"].most_common(5)
        ]
        mood_tags = [
            mood for mood, _ in stats["mood_tags"].most_common(3)
        ]
        all_tags = list(set(style_tags + mood_tags))

        # Build vector text: name + personality + speech style + samples
        vec_parts = [name]
        if identity and identity.aliases:
            vec_parts.append(f"别名：{'、'.join(list(identity.aliases)[:5])}")
        if personality:
            vec_parts.append(f"性格：{personality}")
        if speech_style:
            vec_parts.append(f"说话风格：{speech_style}")
        if background:
            vec_parts.append(f"背景：{background[:100]}")
        vec_parts.extend(sample_dialogues[:3])
        vec_text = " ".join(vec_parts)

        # All person names: canonical + aliases
        all_names = [name]
        if identity and identity.aliases:
            all_names.extend(identity.aliases)

        return NovelBlock(
            global_id=f"{doc_id}_char_{name}",
            doc_id=doc_id,
            source=f"《{doc_id}》角色",
            block_type=BLOCK_CHARACTER,
            character_name=name,  # Legacy field (backward compat)
            character_identity=identity,  # v2.0 structured identity
            personality_profile=personality_profile,  # v2.0 structured profile
            personality=personality,  # Legacy compat text
            speech_style=speech_style,  # Legacy compat text
            background=background,
            relationships=relationships,
            sample_dialogues=sample_dialogues,
            style_tags=all_tags,
            vec_text_character=vec_text,
            all_person=all_names,
            token_length=len(vec_text),
        )

    # ── v2.0: Structured PersonalityProfile extraction ─────

    async def _extract_personality_profile(
        self,
        name: str,
        narrative_snippets: list[str],
        dialogue_contents: list[str],
        llm_client,
    ) -> PersonalityProfile:
        """Extract structured PersonalityProfile via LLM (DeepSeek API).

        Uses the _PERSONALITY_EXTRACTION_PROMPT to produce 6-dim traits,
        5-dim SpeechStyle, catchphrases, and emotional tendencies.
        """
        narrative_text = "。\n".join(narrative_snippets[:5])[:1500]
        dialogue_text = "\n".join(f"- {c}" for c in dialogue_contents[:15])[:1500]

        user_prompt = (
            f"角色名：{name}\n\n"
            f"叙事片段（原文）：\n{narrative_text}\n\n"
            f"对话内容（原文）：\n{dialogue_text}"
        )

        response = await llm_client.achat(
            messages=[
                {"role": "system", "content": _PERSONALITY_EXTRACTION_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=1024,
        )

        data = _parse_llm_json(response)
        return self._dict_to_personality_profile(data, name)




async def _llm_filter_characters(char_stats: dict) -> dict:
    """Use LLM to filter out non-character names from a large candidate list."""
    import json as _json
    import os
    names = list(char_stats.keys())
    prompt = (
        f"以下是小说中提取的候选角色名（共{len(names)}个）：\n" +
        "\n".join(names[:100]) +
        "\n\n请判断哪些是真实人物角色名。用JSON数组回复：\n[\"角色1\",\"角色2\",...]"
    )
    key = os.getenv("DEEPSEEK_API_KEY", "")
    if not key:
        raise RuntimeError("no API key")
    from src.shared.llm import SharedLLMClient
    llm = SharedLLMClient(
        primary={"api_key": key, "base_url": "https://api.deepseek.com", "model": "deepseek-v4-flash"},
        temperature=0.1, max_tokens=500,
    )
    resp = await llm.achat([
        {"role": "system", "content": "你是文学角色分析助手。只回复JSON数组。"},
        {"role": "user", "content": prompt},
    ])
    match = re.search(r'\[.*?\]', resp, re.DOTALL)
    if match:
        valid_names = set(_json.loads(match.group()))
        return {n: s for n, s in char_stats.items() if n in valid_names}
    return char_stats
