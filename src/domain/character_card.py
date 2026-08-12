"""Character card — persona profile for ImpersonationAgent.

v2.0: Structured fields (traits, speech_patterns, structured_catchphrases)
from PersonalityProfile. to_prompt() uses structured data when available,
falling back to legacy text fields.

Loads from JSON cache (data/characters/{name}.json) or auto-builds
from NovelVectorStore by searching narrative + dialogue channels.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

logger = logging.getLogger("agent")


@dataclass
class CharacterCard:
    """Immutable persona profile for a novel character.

    v2.0: Added structured fields from PersonalityProfile for richer prompts.
    Can be loaded from a JSON cache or built from NovelVectorStore.
    The ``to_prompt()`` method produces a system message for the LLM.
    """

    name: str
    source_work: str = ""
    personality: str = ""
    speaking_style: str = ""
    background: str = ""
    relationships: str = ""
    catchphrases: list[str] = field(default_factory=list)
    sample_dialogues: list[dict] = field(default_factory=list)

    # ── v2.0 structured fields ──────────────────────────
    traits: dict[str, float] = field(default_factory=dict)  # 6-dim scores
    speech_patterns: dict = field(default_factory=dict)      # SpeechStyle.to_dict()
    structured_catchphrases: list[str] = field(default_factory=list)

    # ── On-demand / series scope ────────────────────────
    series_id: str = ""
    character_id: str = ""
    aliases: list[str] = field(default_factory=list)
    source_doc_ids: list[str] = field(default_factory=list)
    evidence_hash: str = ""
    prompt_version: str = ""
    stale: bool = False
    low_evidence: bool = False

    # ── Relation projection (单一事实源 story_analysis 的投影) ──
    # relation_refs: 引用的 RelationChange.change_id 列表（可展开证据）
    # relations_view: 结构化关系视图（构建/刷新时从 relation_store 派生，
    #                 不独立 LLM 蒸馏，保证与检索/图谱一致）
    relation_refs: list[str] = field(default_factory=list)
    relations_view: list[dict] = field(default_factory=list)

    # ── Class-level cache ──────────────────────────────────

    _CACHE_DIR: ClassVar[Path] = Path(__file__).resolve().parent.parent.parent / "data" / "characters"
    # {stem → path} and {name-part → path} of every cached card; built with ONE
    # directory scan and reused so per-character load() never globs again.
    _NAME_INDEX: ClassVar[dict[str, Path] | None] = None
    _INDEX_LOCK: ClassVar[Any] = None  # threading.Lock (lazy; avoid import cycle at class def)

    @classmethod
    def _index_lock(cls):
        if cls._INDEX_LOCK is None:
            import threading

            cls._INDEX_LOCK = threading.Lock()
        return cls._INDEX_LOCK

    @classmethod
    def _name_index(cls) -> dict[str, Path]:
        """Lazy {query → path} index over the card cache directory.

        Keys are both the full stem (character_id style, e.g.
        ``Re：从零开始的异世界生活__爱蜜莉雅``) and the trailing name part
        (``爱蜜莉雅``), mirroring the old ``glob('*__{name}.json')`` fallback.
        First match wins (sorted order), matching the previous glob semantics.
        Refreshed on save; external file writes bypassing ``save_for_series``
        are not tracked (call ``invalidate_card_index()`` if needed).
        Built under a lock so concurrent threads never race on the scan.
        """
        if cls._NAME_INDEX is None:
            with cls._index_lock():
                if cls._NAME_INDEX is None:  # double-checked
                    index: dict[str, Path] = {}
                    if cls._CACHE_DIR.exists():
                        for p in sorted(cls._CACHE_DIR.glob("*.json")):
                            stem = p.stem
                            index.setdefault(stem, p)
                            index.setdefault(stem.split("__")[-1], p)
                    cls._NAME_INDEX = index
        return cls._NAME_INDEX

    @classmethod
    def invalidate_card_index(cls) -> None:
        """Drop the cached name index (force a rescan on next load)."""
        cls._NAME_INDEX = None

    @classmethod
    def cache_path_for(
        cls,
        series_id: str,
        canonical_name: str,
        *,
        character_id: str = "",
    ) -> Path:
        """Preferred path: ``{character_id}.json`` or ``{series}__{name}.json``."""
        if character_id:
            fname = f"{character_id}.json"
        elif series_id:
            from src.domain.novel.character_roster import character_id_for
            fname = f"{character_id_for(series_id, canonical_name)}.json"
        else:
            fname = f"{canonical_name}.json"
        return cls._CACHE_DIR / fname

    @classmethod
    def load(cls, character: str) -> CharacterCard | None:
        """Load a cached character card from JSON (index-backed, no glob).

        Resolves ``{character}.json`` (full stem / character_id) or any
        ``*__{character}.json`` via the one-time directory index.
        """
        character = (character or "").strip()
        if not character:
            return None
        cache_path = cls._name_index().get(character)
        if cache_path is None:
            return None
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            return cls.from_dict(data)
        except (json.JSONDecodeError, KeyError, OSError) as e:
            # The index may be stale (card deleted by merge/cleanup); drop the
            # entry so repeated loads stop failing against a missing file.
            if isinstance(e, OSError) and not cache_path.exists():
                index = cls._name_index()
                index.pop(character, None)
                index.pop(str(cache_path.stem), None)
            logger.warning("Failed to load character card for '%s': %s", character, e)
            return None

    @classmethod
    def load_for_series(
        cls,
        series_id: str,
        canonical_name: str,
        *,
        character_id: str = "",
    ) -> CharacterCard | None:
        path = cls.cache_path_for(series_id, canonical_name, character_id=character_id)
        if path.exists():
            try:
                return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, KeyError, OSError) as e:
                logger.warning("Failed to load series card %s: %s", path, e)
        return cls.load(canonical_name)

    @classmethod
    def save_for_series(
        cls,
        series_id: str,
        canonical_name: str,
        card: CharacterCard,
        *,
        character_id: str = "",
    ) -> Path:
        path = cls.cache_path_for(series_id, canonical_name, character_id=character_id)
        cls._CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(card.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        # Keep the name index in sync so subsequent load() sees the new card.
        if cls._NAME_INDEX is not None:
            stem = path.stem
            cls._NAME_INDEX.setdefault(stem, path)
            cls._NAME_INDEX.setdefault(stem.split("__")[-1], path)
        logger.info("Saved series character card: %s", path)
        return path

    @classmethod
    async def build(
        cls,
        character: str,
        store,  # NovelVectorStore (lazy import to avoid circular deps)
        *,
        force_rebuild: bool = False,
        series_id: str = "",
    ) -> CharacterCard:
        """Build a character card from NovelVectorStore.

        v2.0: Reads PersonalityProfile from character block in the store
        for structured traits and speech patterns.

        Args:
            character: Character name (e.g. "林晚晴").
            store: NovelVectorStore instance.
            force_rebuild: If True, skip cache and rebuild from store.
            series_id: Optional series scope. When provided, the card is
                cached as ``{series}__{name}.json`` (never a bare-name card),
                preventing cross-series ghost cards (doc_id/系列缺失时
                `store.doc_ids()[0]` 首卷 fallback 产生错误系列卡的老漏洞).
        """
        # Try cache first — series-scoped when series_id is known
        if not force_rebuild:
            if series_id:
                cached = cls.load_for_series(series_id, character)
            else:
                cached = cls.load(character)
            if cached is not None:
                logger.info("Loaded cached character card for '%s'", character)
                return cached

        logger.info("Building character card for '%s' from vector store", character)

        card = cls(name=character)
        card.series_id = series_id or ""
        try:
            card.source_work = series_id or (store.doc_ids()[0] if store.doc_ids() else "")
        except Exception:
            pass

        # ── v2.0: Read structured PersonalityProfile from store ──
        try:
            doc_ids = store.doc_ids()
            if doc_ids:
                # Try each doc_id for the character block
                for did in doc_ids:
                    char_block = store.get_block(f"{did}_char_{character}")
                    if char_block and char_block.personality_profile:
                        profile = char_block.personality_profile
                        card.traits = dict(profile.traits)
                        card.speech_patterns = profile.speech_patterns.to_dict()
                        card.structured_catchphrases = list(profile.catchphrases)
                        card.personality = _profile_to_legacy_personality(profile)
                        card.speaking_style = _profile_to_legacy_speech_style(profile)
                        logger.info(
                            "Loaded PersonalityProfile for '%s': traits=%s",
                            character, {k: round(v, 2) for k, v in card.traits.items()},
                        )
                        break
        except Exception as e:
            logger.debug("Could not read PersonalityProfile for '%s': %s", character, e)

        # 1. Narrative → background + personality (fallback if no structured data)
        try:
            # Get known character names for dynamic relationship extraction
            known_characters = store.list_characters() if hasattr(store, 'list_characters') else []
            narratives = await store.search(character, channel="narrative", top_k=5)
            if narratives:
                all_text = " ".join(h.block.narrative_text for h in narratives)
                card.background = _summarize_background(all_text, character)
                if not card.personality:
                    card.personality = _infer_personality(all_text)
                card.relationships = _extract_relationships(all_text, character, known_characters)
        except Exception as e:
            logger.warning("Narrative search failed for '%s': %s", character, e)

        # 2. Dialogue → curated speech samples (≤8 clean turns)
        try:
            dialogues = await store.search(
                f"{character} 对话", channel="dialogue", top_k=8,
            )
            if dialogues:
                turns: list[dict] = []
                for h in dialogues:
                    for t in h.block.dialogues:
                        if t.speaker == character or character in (t.speaker or ""):
                            turns.append({
                                "speaker": t.speaker,
                                "content": t.content,
                                "context": h.block.scene or "",
                            })
                card.sample_dialogues = _curate_dialogue_samples(
                    turns, character, max_n=8,
                )
                if not card.speaking_style:
                    card.speaking_style = _infer_speaking_style(card.sample_dialogues)
                if not card.catchphrases:
                    card.catchphrases = _extract_catchphrases(card.sample_dialogues)
        except Exception as e:
            logger.warning("Dialogue search failed for '%s': %s", character, e)

        # 3. Cache — but only if we found meaningful data.
        # 有 series_id 时走系列卡路径（{series}__{name}.json），绝不写裸名卡——
        # 裸名卡会让 load(character) 索引歧义（幽灵卡劫持正确卡）。
        if card.personality or card.speaking_style or card.background or card.traits:
            if series_id:
                cls.save_for_series(series_id, character, card)
            else:
                cls._save_cache(character, card)
        else:
            # Fallback: generate a minimal generic card so the agent can still work.
            # 只返回内存态 placeholder，绝不写盘——避免污染 data/characters
            # （写盘会让前端误显示“已建卡”，且清理残留）。
            logger.info("No character data found for '%s' in store — using in-memory placeholder", character)
            card.personality = "未知（请先导入包含该角色的小说）"
            card.speaking_style = "请先导入小说数据以获得准确的角色风格"
            card.background = f"{character}（角色数据待导入）"
        return card

    def to_prompt(self) -> str:
        """Build the system prompt for role-playing this character.

        v2.0: Uses structured traits (6-dim) and speech_patterns (5-dim)
        when available, falling back to legacy text fields.
        """
        parts = [
            f"你是{self.name}。",
        ]
        if self.source_work:
            parts.append(f"你是小说《{self.source_work}》中的角色。")

        parts.append("")

        # ── 性格特征（优先使用结构化 traits）──
        if self.traits:
            parts.append("## 性格特征")
            parts.append(self._describe_traits(self.traits))
        elif self.personality:
            parts.append(f"## 性格特征\n{self.personality}")

        if self.background:
            parts.append(f"\n## 背景\n{self.background}")

        # ── 说话风格（优先使用结构化 speech_patterns）──
        if self.speech_patterns and any(self.speech_patterns.values()):
            parts.append("\n## 说话风格")
            parts.append(self._describe_speech_patterns(self.speech_patterns))
        elif self.speaking_style:
            parts.append(f"\n## 说话风格\n{self.speaking_style}")

        if self.relations_view:
            parts.append("\n## 人际关系（依据原著证据）")
            for r in self.relations_view[:10]:
                name = str(r.get("name") or "?")
                typ = str(r.get("relation_type") or r.get("category") or "未标注")
                pol = str(r.get("polarity") or "")
                ev = int(r.get("evidence_count") or 0)
                suffix = f"（{pol}）" if pol and pol != "neutral" else ""
                parts.append(f"- 与{name}：{typ}{suffix}" + (f"（证据{ev}处）" if ev else ""))
        elif self.relationships:
            parts.append(f"\n## 人际关系\n{self.relationships}")

        # ── 口头禅 ──
        phrases = self.structured_catchphrases or self.catchphrases
        if phrases:
            p_text = "、".join(f"「{p}」" for p in phrases[:5])
            parts.append(f"\n## 经典台词\n{p_text}")

        if self.sample_dialogues:
            parts.append("\n## 对话样本")
            for i, d in enumerate(self.sample_dialogues[:5], 1):
                ctx = f"（{d.get('context', '')}）" if d.get("context") else ""
                parts.append(f"{i}. [{d['speaker']}] {d['content']} {ctx}")

        parts.append("\n## 规则")
        parts.append(f"- 用{self.name}的语气和视角回复，保持角色一致性")
        parts.append("- 只基于原著设定与下方参考回复；不知道的情节不要编造")
        parts.append("- 可以表达情感，但必须符合角色性格")
        parts.append("- 用户用现代口语时，仍用符合角色的方式回应")
        parts.append("- 优先模仿「对话样本 / 经典台词」的句式与用词，不要整句照搬")

        return "\n".join(parts)

    # ── v2.0: Structured trait/speech descriptions ──────

    @staticmethod
    def _describe_traits(traits: dict[str, float]) -> str:
        """Convert 6-dim trait scores to natural language."""
        descriptions = {
            "extraversion": ("内向孤僻", "偏内向", "适中", "偏外向", "外向活跃"),
            "agreeableness": ("冷漠疏离", "偏冷淡", "适中", "偏友善", "温和体贴"),
            "conscientiousness": ("随性散漫", "偏随性", "适中", "偏认真", "严谨自律"),
            "neuroticism_reverse": ("情绪波动大", "偏敏感", "适中", "偏稳定", "冷静自持"),
            "dominance": ("顺从被动", "偏顺从", "适中", "偏主动", "掌控主导"),
            "complexity": ("单纯直接", "偏简单", "适中", "偏复杂", "心思深沉"),
        }
        dim_names = {
            "extraversion": "外向性", "agreeableness": "宜人性",
            "conscientiousness": "尽责性", "neuroticism_reverse": "情绪稳定性",
            "dominance": "支配性", "complexity": "思维复杂度",
        }
        lines = []
        for dim, score in sorted(traits.items(), key=lambda x: -x[1]):
            name = dim_names.get(dim, dim)
            bucket = max(0, min(4, int(score * 4)))
            desc = descriptions.get(dim, ("低", "", "中", "", "高"))[bucket]
            lines.append(f"- {name}：{desc} ({score:.0%})")
        return "\n".join(lines) if lines else "（未提取）"

    @staticmethod
    def _describe_speech_patterns(sp: dict) -> str:
        """Convert SpeechStyle dict to natural language."""
        lines = []
        if sp.get("vocabulary"):
            lines.append(f"- 用词特征：{sp['vocabulary']}")
        if sp.get("sentence_pattern"):
            lines.append(f"- 句式特点：{sp['sentence_pattern']}")
        if sp.get("catchphrase"):
            lines.append(f"- 口头禅：{sp['catchphrase']}")
        if sp.get("emotional_expression"):
            lines.append(f"- 情绪表达：{sp['emotional_expression']}")
        if sp.get("rhythm"):
            lines.append(f"- 语言节奏：{sp['rhythm']}")
        return "\n".join(lines) if lines else "（未提取）"

    # ── Relation projection refresh ─────────────────────

    def refresh_relations(self, *, series_id: str = "") -> bool:
        """Refresh the relation view from the fact source (story_analysis).

        Lightweight: no LLM, no persona rebuild. Reads the series snapshot,
        re-derives relations_view + relation_refs, clears ``stale`` when a
        snapshot exists. Returns True when refreshed (or already current);
        False when no snapshot exists (caller may fall back to full rebuild).
        """
        sid = series_id or self.series_id
        if not sid:
            return False
        try:
            from src.domain.novel.relation_store import (
                load_snapshot,
                relations_for_character,
                relations_view_for_card,
            )

            snap = load_snapshot(sid)
            if snap is None:
                return False
            rels = relations_for_character(sid, self.name, self.aliases)
            self.relation_refs = [r.change_id for r in rels]
            self.relations_view = relations_view_for_card(sid, self.name, self.aliases)
            if self.stale:
                self.stale = False
            return True
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "refresh_relations(%s) failed: %s", self.name, exc
            )
            return False

    @classmethod
    def refresh_card_relations(
        cls, series_id: str, canonical_name: str, *, character_id: str = ""
    ) -> bool:
        """Load a card (if any) and refresh its relation view in place."""
        card = cls.load_for_series(series_id, canonical_name, character_id=character_id)
        if card is None:
            return False
        if card.refresh_relations(series_id=series_id):
            cls.save_for_series(series_id, canonical_name, card, character_id=character_id)
            return True
        return False

    # ── Serialization ───────────────────────────────────

    def to_dict(self) -> dict:
        """Serialize to dict for JSON caching (v2.0 with structured fields)."""
        return {
            "profile": {
                "name": self.name,
                "source_work": self.source_work,
                "personality": self.personality,
                "speaking_style": self.speaking_style,
                "background": self.background,
                "relationships": self.relationships,
                "catchphrases": self.catchphrases,
                # v2.0 structured
                "traits": self.traits,
                "speech_patterns": self.speech_patterns,
                "structured_catchphrases": self.structured_catchphrases,
                "series_id": self.series_id,
                "character_id": self.character_id,
                "aliases": self.aliases,
                "source_doc_ids": self.source_doc_ids,
                "evidence_hash": self.evidence_hash,
                "prompt_version": self.prompt_version,
                "stale": self.stale,
                "low_evidence": self.low_evidence,
                # relation projection
                "relation_refs": self.relation_refs,
                "relations_view": self.relations_view,
            },
            "dialogues": self.sample_dialogues,
        }

    @classmethod
    def from_dict(cls, data: dict) -> CharacterCard:
        """Deserialize from dict (v2.0 with structured fields + legacy compat)."""
        profile = data.get("profile", data)  # support both new and legacy formats
        return cls(
            name=profile.get("name", ""),
            source_work=profile.get("source_work", ""),
            personality=profile.get("personality", ""),
            speaking_style=profile.get("speaking_style", ""),
            background=profile.get("background", ""),
            relationships=profile.get("relationships", ""),
            catchphrases=profile.get("catchphrases", []),
            # v2.0
            traits=profile.get("traits", {}),
            speech_patterns=profile.get("speech_patterns", {}),
            structured_catchphrases=profile.get("structured_catchphrases", []),
            sample_dialogues=data.get("dialogues", []),
            series_id=profile.get("series_id", ""),
            character_id=profile.get("character_id", ""),
            aliases=list(profile.get("aliases") or []),
            source_doc_ids=list(profile.get("source_doc_ids") or []),
            evidence_hash=profile.get("evidence_hash", ""),
            prompt_version=profile.get("prompt_version", ""),
            stale=bool(profile.get("stale", False)),
            low_evidence=bool(profile.get("low_evidence", False)),
            relation_refs=list(profile.get("relation_refs") or []),
            relations_view=list(profile.get("relations_view") or []),
        )

    @classmethod
    def _save_cache(cls, character: str, card: CharacterCard) -> None:
        """Save character card as JSON cache."""
        cls._CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_path = cls._CACHE_DIR / f"{character}.json"
        try:
            cache_path.write_text(
                json.dumps(card.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.info("Saved character card cache: %s", cache_path)
        except OSError as e:
            logger.warning("Failed to cache character card: %s", e)


# ── Legacy helpers (fallback when no structured data) ────


def _curate_dialogue_samples(
    turns: list[dict],
    character: str,
    *,
    max_n: int = 8,
) -> list[dict]:
    """Select ≤max_n clean, diverse dialogue samples for impersonation.

    Scoring favors: correct speaker, mid length, unique content, diverse scenes.
    Drops noise speakers, empty/tiny/huge lines, near-duplicates.
    """
    try:
        from src.domain.novel.dialogue_local_llm import is_noise_speaker
    except Exception:
        def is_noise_speaker(name: str) -> bool:  # type: ignore
            return not name or name == "未知"

    scored: list[tuple[float, dict]] = []
    seen_norm: set[str] = set()
    contexts: set[str] = set()

    for t in turns:
        sp = (t.get("speaker") or "").strip()
        content = (t.get("content") or "").strip()
        ctx = (t.get("context") or "").strip()
        if not content or is_noise_speaker(sp):
            continue
        if character not in sp and sp != character:
            continue
        n = len(content)
        if n < 4 or n > 120:
            continue
        norm = "".join(content.split())
        if norm in seen_norm:
            continue
        # near-duplicate: share >80% char overlap with an accepted sample
        if any(
            len(set(norm) & set(s)) / max(len(set(norm) | set(s)), 1) > 0.8
            for s in seen_norm
        ):
            continue

        score = 0.0
        # prefer exact speaker match
        score += 3.0 if sp == character else 1.0
        # prefer mid-length lines (8–40 chars)
        if 8 <= n <= 40:
            score += 2.0
        elif 4 <= n <= 60:
            score += 1.0
        # diversify scenes
        if ctx and ctx not in contexts:
            score += 1.5
        # slight preference for questions / catchphrase-like punctuation
        if "？" in content or "?" in content or "…" in content or "..." in content:
            score += 0.5

        seen_norm.add(norm)
        if ctx:
            contexts.add(ctx)
        scored.append((score, {
            "speaker": sp,
            "content": content,
            "context": ctx,
        }))

    scored.sort(key=lambda x: -x[0])
    return [t for _, t in scored[:max_n]]


def _profile_to_legacy_personality(profile) -> str:
    """Convert PersonalityProfile to legacy text (fallback)."""
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


def _profile_to_legacy_speech_style(profile) -> str:
    """Convert PersonalityProfile.speech_patterns to legacy text (fallback)."""
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


def _summarize_background(text: str, character: str) -> str:
    """Extract background info about a character from narrative text."""
    sentences = text.replace("\n", "").split("。")
    relevant = [s for s in sentences if character in s]
    summary = "。".join(relevant[:3])
    return summary[:400] if summary else text[:300]


def _infer_personality(text: str) -> str:
    """[LEGACY] Infer personality traits from descriptive text using keyword heuristics."""
    traits = []
    kw_map = {
        "清冷": ["清冷", "淡漠", "淡然", "疏离", "不言不语", "不动声色"],
        "温柔": ["温柔", "柔和", "体贴", "轻声", "含笑"],
        "活泼": ["笑", "跳", "跑", "热闹", "叽叽喳喳", "活泼"],
        "坚毅": ["咬牙", "握紧", "坚定", "绝不", "一定"],
        "沉默": ["沉默", "不说", "默然", "安静", "不语"],
        "聪慧": ["聪", "慧", "敏锐", "察觉", "发现"],
        "倔强": ["倔", "固执", "偏要", "我不要"],
    }
    for trait, keywords in kw_map.items():
        if any(kw in text for kw in keywords):
            traits.append(trait)
    return "、".join(traits) if traits else "性格特征未明确"


def _extract_relationships(text: str, character: str, known_characters: list[str] | None = None) -> str:
    """Extract relationship clues from narrative text.

    Uses known character names when available (from NovelVectorStore).
    Falls back to dynamic CJK name extraction when no list is provided.
    """
    import re

    # Use known characters from the store if available (most reliable)
    if known_characters:
        relations = []
        for name in known_characters:
            if name == character:
                continue
            pattern = re.compile(rf"[^。]*{re.escape(character)}[^。]*{re.escape(name)}[^。]*。")
            for m in pattern.finditer(text):
                relations.append(f"{name}：" + m.group(0).strip()[:80])
                if len(relations) >= 3:
                    break
            if len(relations) >= 3:
                break
        return "；".join(relations) if relations else ""

    # Fallback: dynamic CJK name extraction from co-occurring sentences
    sentences = text.replace("\n", "").split("。")
    co_names: dict[str, int] = {}
    for sent in sentences:
        if character not in sent:
            continue
        for m in re.finditer(r"[\u4e00-\u9fff]{2,3}", sent):
            name = m.group()
            if name == character:
                continue
            if name in _NON_NAME_PATTERNS:
                continue
            co_names[name] = co_names.get(name, 0) + 1

    relations = []
    for name, count in sorted(co_names.items(), key=lambda x: -x[1]):
        if count < 2:
            continue
        for sent in sentences:
            if character in sent and name in sent:
                relations.append(f"{name}：" + sent.strip()[:80])
                break
        if len(relations) >= 3:
            break

    return "；".join(relations) if relations else ""


# Common CJK bigrams/trigrams that look like names but aren't
_NON_NAME_PATTERNS = frozenset({
    "自己", "他们", "我们", "你们", "她们", "什么", "怎么", "为什么",
    "可以", "没有", "已经", "不是", "因为", "所以", "如果", "虽然",
    "但是", "而且", "然后", "不过", "还是", "只是", "这个", "那个",
    "知道", "觉得", "看见", "听到", "感觉", "忽然", "突然", "慢慢",
    "轻轻", "微微", "淡淡", "冷冷", "一声", "一点", "一下", "起来",
    "下来", "上去", "过来", "过去", "说道", "问道", "笑道", "看着",
    "回头", "转身", "点头", "摇头", "心中", "眼前", "身边", "四周",
})


def _infer_speaking_style(turns: list[dict]) -> str:
    """[LEGACY] Infer speaking style from dialogue turns."""
    if not turns:
        return ""

    contents = [t.get("content", "") for t in turns if t.get("content")]
    if not contents:
        return ""

    avg_len = sum(len(c) for c in contents) / len(contents)

    styles = []
    if avg_len < 10:
        styles.append("简洁短促，常用单句或短句")
    elif avg_len > 30:
        styles.append("表达完整，句子较长，善用修饰")
    else:
        styles.append("表达适中")

    all_text = "".join(contents)
    if all_text.count("？") > len(contents) * 0.3:
        styles.append("常使用反问句")
    if all_text.count("！") > len(contents) * 0.3:
        styles.append("语气强烈，多用感叹")
    if "……" in all_text or "..." in all_text:
        styles.append("语句间有停顿，说话有所保留")

    return "；".join(styles) if styles else "说话风格未分类"


def _extract_catchphrases(turns: list[dict]) -> list[str]:
    """[LEGACY] Extract potential catchphrases (short, distinctive lines)."""
    contents = [t.get("content", "") for t in turns if t.get("content")]
    candidates = [c for c in contents if 5 <= len(c) <= 25]
    seen = set()
    unique = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            unique.append(c)
    return unique[:5]
