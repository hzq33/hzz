"""Character card persistence for on-demand builds.

Extracted from the former monolithic ``character_on_demand.py``; logic unchanged.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from src.domain.novel.character_on_demand.models import EvidencePack
from src.domain.novel.character_roster import (
    CharacterRoster,
    load_roster,
    save_roster,
)

logger = logging.getLogger("agent")


def persist_card(
    *,
    series_id: str,
    character_id: str,
    canonical_name: str,
    aliases: list[str],
    profile: dict[str, Any],
    evidence: EvidencePack,
    source_doc_ids: list[str],
    low_evidence: bool,
) -> tuple[Any, Path]:
    from src.domain.character_card import CharacterCard, _curate_dialogue_samples

    speech = profile.get("speech") or {}
    traits = profile.get("traits") or {}
    # coerce trait floats
    clean_traits = {}
    for k, v in traits.items():
        try:
            clean_traits[k] = round(float(v), 2)
        except (TypeError, ValueError):
            continue

    samples = _curate_dialogue_samples(
        [
            {
                "speaker": d.get("speaker", canonical_name),
                "content": d.get("content", ""),
                "context": d.get("context", ""),
            }
            for d in evidence.dialogues
        ],
        canonical_name,
        max_n=8,
    )
    # If curator filtered too hard (alias speakers), keep raw top
    if not samples and evidence.dialogues:
        samples = [
            {
                "speaker": d.get("speaker", canonical_name),
                "content": d.get("content", ""),
                "context": d.get("context", ""),
            }
            for d in evidence.dialogues[:8]
        ]

    speaking_style = ""
    if speech:
        parts = [speech.get(k, "") for k in ("vocabulary", "sentence_pattern", "rhythm", "emotional_expression")]
        speaking_style = "；".join(p for p in parts if p)

    # 关系投影：从事实源（story_analysis 快照）派生，不独立 LLM 蒸馏。
    # 保证卡片与 character 通道检索 / 关系图谱读同一份关系数据。
    relation_refs: list[str] = []
    relations_view: list[dict] = []
    try:
        from src.domain.novel.relation_store import (
            relations_for_character,
            relations_view_for_card,
        )

        rels = relations_for_character(series_id, canonical_name, aliases)
        if rels:
            relation_refs = [r.change_id for r in rels]
            relations_view = relations_view_for_card(series_id, canonical_name, aliases)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Relation projection failed for %s: %s", canonical_name, exc)

    card = CharacterCard(
        name=canonical_name,
        source_work=series_id,
        personality=str(profile.get("personality") or ""),
        speaking_style=speaking_style or str(profile.get("emotional_tendencies") or ""),
        background=str(profile.get("background") or ""),
        relationships=str(profile.get("relationships") or ""),
        catchphrases=list(profile.get("catchphrases") or []),
        traits=clean_traits,
        speech_patterns=dict(speech) if isinstance(speech, dict) else {},
        structured_catchphrases=list(profile.get("catchphrases") or []),
        sample_dialogues=samples,
        series_id=series_id,
        character_id=character_id,
        aliases=aliases,
        source_doc_ids=source_doc_ids,
        evidence_hash=evidence.fingerprint(),
        prompt_version="persona_v3_on_demand",
        stale=False,
        low_evidence=low_evidence,
        relation_refs=relation_refs,
        relations_view=relations_view,
    )
    path = CharacterCard.save_for_series(series_id, canonical_name, card, character_id=character_id)

    # Update roster
    roster = load_roster(series_id) or CharacterRoster(series_id=series_id, doc_ids=list(source_doc_ids))
    entry = roster.find(canonical_name)
    from src.domain.novel.character_roster import RosterEntry

    if entry is None:
        entry = RosterEntry(name=canonical_name)
        roster.characters.append(entry)
    entry.character_id = character_id
    entry.has_card = True
    entry.status = "low_evidence" if low_evidence else "ready"
    entry.aliases_observed = sorted(set(entry.aliases_observed) | set(aliases) - {canonical_name})
    save_roster(roster)
    return card, path


