"""Story analysis data models — evidence / events / foreshadows / relations.

Extracted from the former monolithic ``story_analysis.py``; logic unchanged.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# Default prompt version for snapshots (kept with models to avoid circular import).
_PROMPT_VERSION = "story_v3"


@dataclass
class StoryEvidence:
    doc_id: str = ""
    chapter_order: int = 0
    chapter_title: str = ""
    block_id: str = ""
    snippet: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StoryEvidence:
        return cls(
            doc_id=str(data.get("doc_id") or ""),
            chapter_order=int(data.get("chapter_order") or 0),
            chapter_title=str(data.get("chapter_title") or ""),
            block_id=str(data.get("block_id") or ""),
            snippet=str(data.get("snippet") or "")[:400],
        )


@dataclass
class StoryEvent:
    event_id: str
    summary: str
    event_type: str = "plot"  # plot|character|world|other
    characters: list[str] = field(default_factory=list)
    confidence: float = 0.0
    evidence: list[StoryEvidence] = field(default_factory=list)
    doc_id: str = ""
    chapter_order: int = 0
    chapter_title: str = ""
    # V5 故事内时间：{"year": int|None, "period": str, "label": str, "relative": str, "confidence": float}
    story_time: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            **{k: v for k, v in asdict(self).items() if k != "evidence"},
            "evidence": [e.to_dict() for e in self.evidence],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StoryEvent:
        return cls(
            event_id=str(data.get("event_id") or ""),
            summary=str(data.get("summary") or ""),
            event_type=str(data.get("event_type") or "plot"),
            characters=list(data.get("characters") or []),
            confidence=float(data.get("confidence") or 0),
            evidence=[StoryEvidence.from_dict(e) for e in (data.get("evidence") or [])],
            doc_id=str(data.get("doc_id") or ""),
            chapter_order=int(data.get("chapter_order") or 0),
            chapter_title=str(data.get("chapter_title") or ""),
            story_time=dict(data.get("story_time") or {}),
        )


@dataclass
class ForeshadowRecord:
    foreshadow_id: str
    content: str
    status: str = "pending"  # pending|resolved|abandoned
    related_characters: list[str] = field(default_factory=list)
    introduced_chapter: int = 0
    introduced_doc_id: str = ""
    resolved_chapter: int | None = None
    resolved_doc_id: str | None = None
    confidence: float = 0.0
    evidence: list[StoryEvidence] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            **{k: v for k, v in asdict(self).items() if k != "evidence"},
            "evidence": [e.to_dict() for e in self.evidence],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ForeshadowRecord:
        return cls(
            foreshadow_id=str(data.get("foreshadow_id") or ""),
            content=str(data.get("content") or ""),
            status=str(data.get("status") or "pending"),
            related_characters=list(data.get("related_characters") or []),
            introduced_chapter=int(data.get("introduced_chapter") or 0),
            introduced_doc_id=str(data.get("introduced_doc_id") or ""),
            resolved_chapter=data.get("resolved_chapter"),
            resolved_doc_id=data.get("resolved_doc_id"),
            confidence=float(data.get("confidence") or 0),
            evidence=[StoryEvidence.from_dict(e) for e in (data.get("evidence") or [])],
        )


@dataclass
class RelationChange:
    change_id: str
    source: str
    target: str
    relation_type: str = ""
    polarity: str = "neutral"  # positive|negative|neutral
    summary: str = ""
    chapter_order: int = 0
    doc_id: str = ""
    chapter_title: str = ""
    confidence: float = 0.0
    evidence: list[StoryEvidence] = field(default_factory=list)
    # V5 故事内时间（同 StoryEvent.story_time）
    story_time: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            **{k: v for k, v in asdict(self).items() if k != "evidence"},
            "evidence": [e.to_dict() for e in self.evidence],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RelationChange:
        return cls(
            change_id=str(data.get("change_id") or ""),
            source=str(data.get("source") or ""),
            target=str(data.get("target") or ""),
            relation_type=str(data.get("relation_type") or ""),
            polarity=str(data.get("polarity") or "neutral"),
            summary=str(data.get("summary") or ""),
            chapter_order=int(data.get("chapter_order") or 0),
            doc_id=str(data.get("doc_id") or ""),
            chapter_title=str(data.get("chapter_title") or ""),
            confidence=float(data.get("confidence") or 0),
            evidence=[StoryEvidence.from_dict(e) for e in (data.get("evidence") or [])],
            story_time=dict(data.get("story_time") or {}),
        )


@dataclass
class StoryAnalysisSnapshot:
    series_id: str
    doc_ids: list[str] = field(default_factory=list)
    content_fingerprint: str = ""
    prompt_version: str = _PROMPT_VERSION
    updated_at: str = ""
    events: list[StoryEvent] = field(default_factory=list)
    foreshadows: list[ForeshadowRecord] = field(default_factory=list)
    relations: list[RelationChange] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "series_id": self.series_id,
            "doc_ids": list(self.doc_ids),
            "content_fingerprint": self.content_fingerprint,
            "prompt_version": self.prompt_version,
            "updated_at": self.updated_at,
            "events": [e.to_dict() for e in self.events],
            "foreshadows": [f.to_dict() for f in self.foreshadows],
            "relations": [r.to_dict() for r in self.relations],
            "stats": dict(self.stats),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StoryAnalysisSnapshot:
        return cls(
            series_id=str(data.get("series_id") or ""),
            doc_ids=list(data.get("doc_ids") or []),
            content_fingerprint=str(data.get("content_fingerprint") or ""),
            prompt_version=str(data.get("prompt_version") or _PROMPT_VERSION),
            updated_at=str(data.get("updated_at") or ""),
            events=[StoryEvent.from_dict(e) for e in (data.get("events") or [])],
            foreshadows=[ForeshadowRecord.from_dict(f) for f in (data.get("foreshadows") or [])],
            relations=[RelationChange.from_dict(r) for r in (data.get("relations") or [])],
            stats=dict(data.get("stats") or {}),
        )


