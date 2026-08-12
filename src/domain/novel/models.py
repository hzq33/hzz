"""Novel domain models — unified structured pipeline (v2.0).

Defines the complete data model for the novel RAG system:
- NovelDocument: a book with chapters and metadata
- NovelBlock: the core indexing record (narrative/dialogue/qa/character)
- CharacterIdentity: structured character identity replacing plain string (NEW v2.0)
- PersonalityProfile: multi-dimensional personality replacing keyword mapping (NEW v2.0)
- SpeechStyle: 5-dimensional speech style analysis (NEW v2.0)
- Pre-computed vector texts for each channel

v2.0 Changes (2026-07-24):
- Added CharacterIdentity with alias management (from Renard)
- Added Gender enum and Mention record
- Enhanced DialogueTurn with confidence/scene/is_indirect
- Added SpeechStyle (5-dim from NovelCorpus) and PersonalityProfile (6-dim from ChatHaruhi)
- Deprecated: narrative_entities (dead code), dialogue_style_text, speaker_count
- Removed: appearance_count, dialogue_count (moved to CharacterIdentity)
- Backward compatible: from_dict() handles old format gracefully
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import ClassVar

# ─ Block Types ────────────────────────────────────────────

BLOCK_NARRATIVE = "narrative"
BLOCK_DIALOGUE = "dialogue"
BLOCK_QA = "qa"
BLOCK_CHARACTER = "character"
BLOCK_TYPES: list[str] = [BLOCK_NARRATIVE, BLOCK_DIALOGUE, BLOCK_QA, BLOCK_CHARACTER]


# ─ Enumerations (NEW v2.0) ────────────────────────────────


class Gender(Enum):
    """Gender enumeration — from Renard character_unification.py."""

    MALE = "male"
    FEMALE = "female"
    UNKNOWN = "unknown"


# ─ Core Identity Types (NEW v2.0) ──────────────────────────


@dataclass
class Mention:
    """Single occurrence record of a character name in text.

    From Renard: tracks position, chapter, and context for each mention.
    Used for alias resolution and frequency analysis.
    """

    text: str = ""
    start: int = 0
    end: int = 0
    chapter: int = 0
    context: str = ""  # ~50 chars surrounding context


@dataclass
class CharacterIdentity:
    """Structured character identity — replaces plain ``character_name`` string.

    Key design decisions (from cross-project comparison):
    - ``canonical_name``: single authoritative name (e.g. "林晚晴")
    - ``aliases``: frozenset of known variations (e.g. {"晚晴", "林姐姐", "晚晴姐"})
    - ``mentions``: list of occurrence records for frequency/position analysis
    - ``gender``: optional enum for relationship inference (from Renard)

    Migration path:
        Old: block.character_name = "林晚晴"
        New: block.character_identity = CharacterIdentity(
                canonical_name="林晚晴",
                aliases=frozenset({"晚晴", "林姐姐"}),
            )
    """

    canonical_name: str = ""  # Authoritative name
    aliases: frozenset[str] = field(default_factory=frozenset)  # Known variations
    mentions: list[Mention] = field(default_factory=list)  # Occurrence records
    gender: Gender | None = None
    first_appearance_ch: int = 0
    total_mentions: int = 0

    @property
    def display_name(self) -> str:
        """Most frequently used name — for UI display."""
        if not self.mentions:
            return self.canonical_name
        from collections import Counter

        names = [m.text for m in self.mentions if m.text]
        if not names:
            return self.canonical_name
        return Counter(names).most_common(1)[0][0]

    @property
    def all_names(self) -> frozenset[str]:
        """Union of canonical name and aliases."""
        return self.aliases | {self.canonical_name}

    def to_dict(self) -> dict:
        """Serialize to JSON-compatible dict."""
        return {
            "canonical_name": self.canonical_name,
            "aliases": list(self.aliases),
            "mentions": [
                {
                    "text": m.text,
                    "start": m.start,
                    "end": m.end,
                    "chapter": m.chapter,
                    "context": m.context,
                }
                for m in self.mentions
            ],
            "gender": self.gender.value if self.gender else None,
            "first_appearance_ch": self.first_appearance_ch,
            "total_mentions": self.total_mentions,
        }

    @classmethod
    def from_dict(cls, data: dict) -> CharacterIdentity:
        """Deserialize from dict. Handles old-format plain-string migration."""
        if isinstance(data, str):
            # Legacy format: plain string → wrap in CharacterIdentity
            return cls(canonical_name=data)

        mentions = []
        for m in data.get("mentions", []):
            mentions.append(
                Mention(
                    text=m.get("text", ""),
                    start=m.get("start", 0),
                    end=m.get("end", 0),
                    chapter=m.get("chapter", 0),
                    context=m.get("context", ""),
                )
            )

        gender_val = data.get("gender")
        gender = Gender(gender_val) if gender_val else None

        return cls(
            canonical_name=data.get("canonical_name", ""),
            aliases=frozenset(data.get("aliases", [])),
            mentions=mentions,
            gender=gender,
            first_appearance_ch=data.get("first_appearance_ch", 0),
            total_mentions=data.get("total_mentions", 0),
        )


# ─ Personality & Style (NEW v2.0) ─────────────────────────


@dataclass
class SpeechStyle:
    """5-dimensional speech style analysis — from NovelCorpus Stylist Agent.

    Dimensions:
    - vocabulary: characteristic words/phrases
    - sentence_pattern: sentence structure tendencies
    - catchphrase: fixed verbal tics / signature phrases
    - emotional_expression: how emotions are conveyed (action vs direct statement)
    - rhythm: pacing and cadence patterns
    """

    vocabulary: str = ""  # e.g. "文言文夹杂白话; 频繁使用反问句"
    sentence_pattern: str = ""  # e.g. "短句为主(<10字); 排比句式多"
    catchphrase: str = ""  # e.g. "本小姐...; 哼，才不是呢"
    emotional_expression: str = ""  # e.g. "用动作掩饰情绪; 语气平淡但内心独白激烈"
    rhythm: str = ""  # e.g. "快节奏对攻时短促有力; 回忆场景节奏放缓"

    def to_dict(self) -> dict:
        return {
            "vocabulary": self.vocabulary,
            "sentence_pattern": self.sentence_pattern,
            "catchphrase": self.catchphrase,
            "emotional_expression": self.emotional_expression,
            "rhythm": self.rhythm,
        }

    @classmethod
    def from_dict(cls, data: dict) -> SpeechStyle:
        if isinstance(data, str):
            # Legacy format: plain string → store in vocabulary
            return cls(vocabulary=data)
        return cls(
            vocabulary=data.get("vocabulary", ""),
            sentence_pattern=data.get("sentence_pattern", ""),
            catchphrase=data.get("catchphrase", ""),
            emotional_expression=data.get("emotional_expression", ""),
            rhythm=data.get("rhythm", ""),
        )


@dataclass
class PersonalityProfile:
    """Multi-dimensional personality profile — replaces keyword-mapped personality.

    Design (fusion of ChatHaruhi + NovelCorpus):
    - traits: 6-dim scores (simplified from ChatHaruhi's 12-factor model)
      Values 0.0–1.0, suitable for LLM structured extraction.
    - speech_patterns: 5-dim style from NovelCorpus Stylist Agent
    - catchphrases: list of signature phrases
    - emotional_tendencies: free-text description

    Trait dimensions (6 selected from ChatHaruhi's 12):
    - extraversion: social activity level
    - agreeableness: cooperation tendency
    - conscientiousness: self-discipline
    - neuroticism_reverse: emotional stability (inverted: higher = more stable)
    - dominance: desire to influence
    - complexity: thinking depth / nuance
    """

    traits: dict[str, float] = field(default_factory=dict)
    speech_patterns: SpeechStyle = field(default_factory=SpeechStyle)
    catchphrases: list[str] = field(default_factory=list)
    emotional_tendencies: str = ""

    # Default trait dimensions
    TRAIT_DIMS: ClassVar[list[str]] = [
        "extraversion",
        "agreeableness",
        "conscientiousness",
        "neuroticism_reverse",
        "dominance",
        "complexity",
    ]

    @property
    def dominant_traits(self) -> list[tuple[str, float]]:
        """Return traits sorted by score descending."""
        sorted_traits = sorted(self.traits.items(), key=lambda x: x[1], reverse=True)
        return [(k, v) for k, v in sorted_traits if v > 0.5]

    def to_dict(self) -> dict:
        return {
            "traits": dict(self.traits),  # Plain dict for JSON
            "speech_patterns": self.speech_patterns.to_dict(),
            "catchphrases": list(self.catchphrases),
            "emotional_tendencies": self.emotional_tendencies,
        }

    @classmethod
    def from_dict(cls, data: dict) -> PersonalityProfile:
        if isinstance(data, str):
            # Legacy format: plain string → store as emotional_tendencies
            return cls(emotional_tendencies=data)

        sp_data = data.get("speech_patterns", {})
        return cls(
            traits=data.get("traits", {}),
            speech_patterns=SpeechStyle.from_dict(sp_data) if sp_data else SpeechStyle(),
            catchphrases=list(data.get("catchphrases", [])),
            emotional_tendencies=data.get("emotional_tendencies", ""),
        )


# ─ Temporal Relations (P2, placeholder) ───────────────────


@dataclass
class ForeshadowItem:
    """Foreshadowing tracking entry — from NovelCorpus Analyst Agent (P2)."""

    id: str = ""
    content: str = ""
    introduced_ch: int = 0
    status: str = "pending"  # pending | resolved | abandoned
    related_characters: list[str] = field(default_factory=list)
    hint_chapters: list[int] = field(default_factory=list)


@dataclass
class TemporalRelation:
    """Time-evolving relationship between two characters — from Renard (P2).

    Replaces static relationships dict with temporal sequence:
    - dweight: per-chapter co-occurrence strength
    - polarity: positive/negative/neutral edge attribute
    - evidence: supporting text fragments for RAG grounding
    """

    source_canonical: str = ""
    target_canonical: str = ""
    chapter_range: tuple[int, int] = (0, 0)
    weight: float = 0.0
    polarity: str = "neutral"  # positive | negative | neutral
    relation_type: str = ""  # family | lover | enemy | mentor | rival | ...
    dweight: list[float] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)


# ─ Legacy Types (deprecated but kept for compat) ────────────


@dataclass
class NarrativeEntities:
    """[DEPRECATED v2.0] Extracted entities from narrative text.

    Was defined but never filled (dead code).
    Will be removed in v2.1 after WorldState implementation.
    Kept for backward compatibility during migration.
    """

    person: list[str] = field(default_factory=list)
    location: list[str] = field(default_factory=list)
    item: list[str] = field(default_factory=list)
    plot_tag: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "person": self.person,
            "location": self.location,
            "item": self.item,
            "plot_tag": self.plot_tag,
        }


# ─ Dialogue Turn (ENHANCED v2.0) ───────────────────────────


@dataclass
class DialogueTurn:
    """A single turn in a multi-turn dialogue block.

    v2.0 enhancements:
    - confidence: extraction reliability score (for cascade architecture)
    - scene: where this dialogue takes place
    - is_indirect: whether this is reported speech (indirect quotation)
    """

    turn: int
    speaker: str
    content: str
    mood: str = ""
    confidence: float = 1.0  # NEW: 0.0–1.0 extraction confidence
    scene: str = ""  # NEW: scene label for this turn
    is_indirect: bool = False  # NEW: indirect speech flag


# ─ NovelBlock (the single-index multi-vector record) ─────


@dataclass
class NovelBlock:
    """Core domain model — one record for all channels.

    v2.0 Migration Guide:
    - OLD: character_name (str) → NEW: character_identity (CharacterIdentity)
    - OLD: personality (str, keyword-mapped) → NEW: personality_profile (PersonalityProfile)
    - OLD: speech_style (str) → NEW: embedded in personality_profile.speech_patterns
    - REMOVED: dialogue_style_text, speaker_count, appearance_count, dialogue_count
    - DEPRECATED: narrative_entities (dead code, kept for compat)
    """

    # ── Primary key & isolation ────────────────────────
    global_id: str = ""
    doc_id: str = ""
    source: str = ""  # e.g. "《星辰之下》第2章 第3-5段"
    chapter_title: str = ""

    # ── Block type discriminator ────────────────────────
    block_type: str = ""  # "narrative" | "dialogue" | "qa" | "character"

    # ══ Narrative channel (block_type=narrative) ═══════
    narrative_text: str = ""
    narrative_entities: NarrativeEntities = field(default_factory=NarrativeEntities)  # DEPRECATED

    # ══ Dialogue channel (block_type=dialogue) ═════════
    scene: str = ""
    scene_detail: str = ""
    characters: list[str] = field(default_factory=list)
    dialogues: list[DialogueTurn] = field(default_factory=list)
    # REMOVED v2.0: speaker_count (use len(dialogues) instead)
    # REMOVED v2.0: dialogue_style_text (redundant with style_tags)
    style_tags: list[str] = field(default_factory=list)
    ref_narrative_id: str = ""

    # ══ QA channel (block_type=qa) ═════════════════════
    question: str = ""
    answer: str = ""
    ref_chunk_ids: list[str] = field(default_factory=list)
    qa_tags: list[str] = field(default_factory=list)

    # ══ Pre-computed vector texts (4 channels) ═════════
    vec_text_narrative: str = ""
    vec_text_dialogue: str = ""
    vec_text_qa: str = ""
    vec_text_character: str = ""

    # ══ Character channel (block_type=character) ════════
    # ── v2.0: NEW structured fields ───────────────────
    character_identity: CharacterIdentity | None = None  # NEW: replaces character_name
    personality_profile: PersonalityProfile | None = None  # NEW: replaces personality+speech_style

    # ── v1.x legacy fields (backward compat) ───────────
    character_name: str = ""  # LEGACY: use character_identity.canonical_name
    personality: str = ""  # LEGACY: use personality_profile
    speech_style: str = ""  # LEGACY: use personality_profile.speech_patterns
    background: str = ""  # P2: fill via WorldState extraction
    relationships: dict = field(default_factory=dict)  # P2: replace with List[TemporalRelation]
    sample_dialogues: list[str] = field(default_factory=list)
    # REMOVED v2.0: appearance_count (use character_identity.total_mentions)
    # REMOVED v2.0: dialogue_count (use len(sample_dialogues))

    # ── Common metadata ─────────────────────────────────
    all_person: list[str] = field(default_factory=list)
    token_length: int = 300
    # Child→Parent hierarchy (optional; empty = flat legacy block)
    granularity: str = ""  # "" | "parent" | "child"
    parent_id: str = ""
    prev_id: str = ""
    next_id: str = ""

    # ── Computed properties (replace removed fields) ────

    @property
    def speaker_count(self) -> int:
        """Number of unique speakers in dialogues. (REPLACES removed field)"""
        if not self.dialogues:
            return 0
        return len(set(d.speaker for d in self.dialogues))

    @property
    def appearance_count(self) -> int:
        """Character mention count. (REPLACES removed field)"""
        if self.character_identity:
            return self.character_identity.total_mentions
        return 0

    @property
    def dialogue_count(self) -> int:
        """Number of sample dialogues. (REPLACES removed field)"""
        return len(self.sample_dialogues)

    # ── Vector text access ──────────────────────────────

    def get_vec_text(self, channel: str) -> str:
        """Get the vector text for a specific channel."""
        if channel == BLOCK_NARRATIVE:
            return self.vec_text_narrative
        elif channel == BLOCK_DIALOGUE:
            return self.vec_text_dialogue
        elif channel == BLOCK_QA:
            return self.vec_text_qa
        elif channel == BLOCK_CHARACTER:
            return self.vec_text_character
        return ""

    # ── Serialization ───────────────────────────────────

    def to_dict(self) -> dict:
        """Convert to JSON-serializable dict. Includes both v2.0 and legacy fields."""
        base = {
            "global_id": self.global_id,
            "doc_id": self.doc_id,
            "source": self.source,
            "chapter_title": self.chapter_title,
            "block_type": self.block_type,
            "narrative_text": self.narrative_text,
            "narrative_entities": self.narrative_entities.to_dict(),
            "scene": self.scene,
            "scene_detail": self.scene_detail,
            "characters": self.characters,
            "dialogues": [
                {
                    "turn": d.turn,
                    "speaker": d.speaker,
                    "content": d.content,
                    "mood": d.mood,
                    "confidence": d.confidence,
                    "scene": d.scene,
                    "is_indirect": d.is_indirect,
                }
                for d in self.dialogues
            ],
            "style_tags": self.style_tags,
            "ref_narrative_id": self.ref_narrative_id,
            "question": self.question,
            "answer": self.answer,
            "ref_chunk_ids": self.ref_chunk_ids,
            "qa_tags": self.qa_tags,
            "vec_text_narrative": self.vec_text_narrative,
            "vec_text_dialogue": self.vec_text_dialogue,
            "vec_text_qa": self.vec_text_qa,
            "vec_text_character": self.vec_text_character,
            # v2.0 new fields
            "character_identity": (
                self.character_identity.to_dict() if self.character_identity else None
            ),
            "personality_profile": (
                self.personality_profile.to_dict() if self.personality_profile else None
            ),
            # Legacy fields (for backward compat with v1.x consumers)
            "character_name": self.character_name,
            "personality": self.personality,
            "speech_style": self.speech_style,
            "background": self.background,
            "relationships": self.relationships,
            "sample_dialogues": self.sample_dialogues,
            "all_person": self.all_person,
            "token_length": self.token_length,
            "granularity": self.granularity,
            "parent_id": self.parent_id,
            "prev_id": self.prev_id,
            "next_id": self.next_id,
        }
        return base

    @classmethod
    def from_dict(cls, data: dict) -> NovelBlock:
        """Create from JSON dict. Handles v1.x and v2.0 formats."""

        # Parse dialogues (with backward compat for missing v2.0 fields)
        dialogues = []
        for d in data.get("dialogues", []):
            dialogues.append(
                DialogueTurn(
                    turn=d.get("turn", 0),
                    speaker=d.get("speaker", ""),
                    content=d.get("content", ""),
                    mood=d.get("mood", ""),
                    confidence=d.get("confidence", 1.0),  # Default full confidence for old data
                    scene=d.get("scene", ""),
                    is_indirect=d.get("is_indirect", False),
                )
            )

        entities = NarrativeEntities(**data.get("narrative_entities", {}))

        # Parse v2.0 fields (None if absent → graceful degradation)
        ci_data = data.get("character_identity")
        character_identity = CharacterIdentity.from_dict(ci_data) if ci_data else None

        pp_data = data.get("personality_profile")
        personality_profile = PersonalityProfile.from_dict(pp_data) if pp_data else None

        return cls(
            global_id=data.get("global_id", ""),
            doc_id=data.get("doc_id", ""),
            source=data.get("source", ""),
            chapter_title=data.get("chapter_title", ""),
            block_type=data.get("block_type", ""),
            narrative_text=data.get("narrative_text", ""),
            narrative_entities=entities,
            scene=data.get("scene", ""),
            scene_detail=data.get("scene_detail", ""),
            characters=data.get("characters", []),
            dialogues=dialogues,
            style_tags=data.get("style_tags", []),
            ref_narrative_id=data.get("ref_narrative_id", ""),
            question=data.get("question", ""),
            answer=data.get("answer", ""),
            ref_chunk_ids=data.get("ref_chunk_ids", []),
            qa_tags=data.get("qa_tags", []),
            vec_text_narrative=data.get("vec_text_narrative", ""),
            vec_text_dialogue=data.get("vec_text_dialogue", ""),
            vec_text_qa=data.get("vec_text_qa", ""),
            vec_text_character=data.get("vec_text_character", ""),
            # v2.0 new
            character_identity=character_identity,
            personality_profile=personality_profile,
            # Legacy
            character_name=data.get("character_name", ""),
            personality=data.get("personality", ""),
            speech_style=data.get("speech_style", ""),
            background=data.get("background", ""),
            relationships=data.get("relationships", {}),
            sample_dialogues=data.get("sample_dialogues", []),
            all_person=data.get("all_person", []),
            token_length=data.get("token_length", 300),
            granularity=data.get("granularity", ""),
            parent_id=data.get("parent_id", ""),
            prev_id=data.get("prev_id", ""),
            next_id=data.get("next_id", ""),
        )

    # ── Migration helpers ────────────────────────────────

    def migrate_to_v2(self) -> None:
        """Upgrade v1.x fields to v2.0 structure in-place.

        Call this after loading old-format data to populate new fields.
        """
        # Migrate character_name → character_identity
        if not self.character_identity and self.character_name:
            self.character_identity = CharacterIdentity(
                canonical_name=self.character_name,
                aliases=frozenset({self.character_name}),
            )

        # Migrate personality/speech_style → personality_profile
        if not self.personality_profile and (self.personality or self.speech_style):
            self.personality_profile = PersonalityProfile(
                emotional_tendencies=self.personality or "",
                speech_patterns=SpeechStyle.from_dict(self.speech_style)
                if self.speech_style
                else SpeechStyle(),
            )


# ─ Chapter ──────────────────────────────────────────────


@dataclass
class Chapter:
    """A single chapter of a novel."""

    chapter_id: str = ""
    title: str = ""
    order: int = 0
    text: str = ""

    def to_dict(self) -> dict:
        return {
            "chapter_id": self.chapter_id,
            "title": self.title,
            "order": self.order,
            "text": self.text,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Chapter:
        return cls(
            chapter_id=data.get("chapter_id", ""),
            title=data.get("title", ""),
            order=data.get("order", 0),
            text=data.get("text", ""),
        )


# ─ NovelDocument ────────────────────────────────────────


@dataclass
class NovelDocument:
    """Structured representation of a novel book.

    This is the unified output of the ingestion pipeline:
    any format (epub/txt/md/pdf) → NovelDocument → NovelBlocks → indexed.
    """

    doc_id: str = ""
    title: str = ""
    author: str = ""
    source_format: str = ""  # epub / txt / md / pdf
    raw_md: str = ""
    chapters: list[Chapter] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    @property
    def total_words(self) -> int:
        return sum(len(ch.text) for ch in self.chapters)

    @property
    def total_chapters(self) -> int:
        return len(self.chapters)

    def to_dict(self) -> dict:
        return {
            "doc_id": self.doc_id,
            "title": self.title,
            "author": self.author,
            "source_format": self.source_format,
            "chapters": [ch.to_dict() for ch in self.chapters],
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> NovelDocument:
        chapters = [Chapter.from_dict(c) for c in data.get("chapters", [])]
        return cls(
            doc_id=data.get("doc_id", ""),
            title=data.get("title", ""),
            author=data.get("author", ""),
            source_format=data.get("source_format", ""),
            chapters=chapters,
            metadata=data.get("metadata", {}),
        )
