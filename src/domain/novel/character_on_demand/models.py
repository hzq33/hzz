"""On-demand character build models.

Extracted from the former monolithic ``character_on_demand.py``; logic unchanged.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class EvidencePack:
    dialogues: list[dict] = field(default_factory=list)
    narratives: list[str] = field(default_factory=list)
    dialogue_hits: int = 0
    narrative_hits: int = 0
    sample_ids: list[str] = field(default_factory=list)

    def fingerprint(self) -> str:
        raw = json.dumps(
            {
                "d": [(x.get("speaker"), x.get("content")) for x in self.dialogues[:40]],
                "n": self.narratives[:15],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@dataclass
class NormalizeResult:
    character_id: str
    canonical_name: str
    aliases: list[str]
    need_disambiguate: bool = False
    candidates: list[dict] = field(default_factory=list)


@dataclass
class CharacterBuildJob:
    job_id: str
    series_id: str
    doc_id: str | None
    input_name: str
    character_id: str = ""
    canonical_name: str = ""
    aliases: list[str] = field(default_factory=list)
    state: str = "pending"  # pending|normalize|gather|distill|persist|done|failed
    evidence: dict[str, Any] = field(default_factory=dict)
    flags: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    card_path: str | None = None
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CharacterBuildJob:
        return cls(**{k: data.get(k) for k in cls.__dataclass_fields__})  # type: ignore


