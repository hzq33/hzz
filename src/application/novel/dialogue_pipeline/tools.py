"""Dialogue pipeline helpers — seed detection, window candidates, turn blocks.

Extracted from the former monolithic ``dialogue_pipeline.py``; logic unchanged.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

logger = logging.getLogger("agent")


def _high_confidence_seeds(spans: Sequence[Any], high_min: float) -> list[str]:
    from src.domain.novel.dialogue_span import is_noise_speaker

    names: list[str] = []
    for sp in spans:
        if (
            not sp.needs_attribution
            and sp.hint_source in ("named_colon", "postfix_said")
            and sp.confidence >= high_min
            and not is_noise_speaker(sp.speaker_hint)
        ):
            if sp.speaker_hint not in names:
                names.append(sp.speaker_hint)
    return names


def _window_local_candidates(text: str, spans: Sequence[Any], *, max_n: int = 6) -> list[str]:
    """Conservative harvest from chapter text for cold-start / extra candidates."""
    from src.domain.novel.dialogue_span import is_noise_speaker
    from src.domain.novel.speaker_attributor import candidates_from_text

    out: list[str] = []
    for name in candidates_from_text(text, max_n=max_n * 2):
        if is_noise_speaker(name):
            continue
        if name not in out:
            out.append(name)
        if len(out) >= max_n:
            break
    for sp in spans:
        if not sp.needs_attribution and not is_noise_speaker(sp.speaker_hint):
            if sp.speaker_hint not in out:
                out.insert(0, sp.speaker_hint)
    return out[:max_n]


def build_alias_prompt_text(series_id: str | None) -> str:
    """Build alias mapping table text for LLM prompt.

    Returns a compact string like:
      "温水→温水和彦, 八奈→八奈见杏菜, ..."
    or empty string when no alias.json exists.
    """
    if not series_id:
        return ""
    from src.domain.novel.alias_map import load_alias_map

    for sid in {series_id, series_id.replace(" ", "_")}:
        try:
            amap = load_alias_map(sid)
        except Exception:
            continue
        if amap is None or not getattr(amap, "entities", None):
            continue
        pairs: list[str] = []
        for e in amap.entities:
            canon = e.canonical_name or ""
            if not canon:
                continue
            for a in e.aliases or []:
                if a and a != canon:
                    pairs.append(f"{a}→{canon}")
        if pairs:
            return "，".join(pairs)
    return ""


def assemble_prompt_candidates(
    *,
    volume_seed: Sequence[str] | None,
    chapter_text: str,
    spans: Sequence[Any] | None = None,
    max_n: int = 10,
    high_min: float = 0.85,
    prefer_local: bool = True,
    chapter_harvest: Sequence[str] | None = None,
    series_id: str | None = None,
) -> list[str]:
    """Build per-window LLM candidate list: local first, volume_seed as prior.

    Order:
      1. high-confidence span seeds (if spans given)
      2. window/text-local harvest (LLM chapter harvest; regex fallback)
      3. volume_seed names that appear in chapter text
      4. cold-start fill from remaining volume_seed (capped)

    ``chapter_harvest`` is the pre-computed LLM harvest for the whole chapter
    (see dialogue_pipeline.harvest); names are re-checked against the window
    text here. None -> legacy regex harvest path (silent fallback).

    ``series_id`` enables alias canonicalisation: the alias mapping table
    is passed to the LLM so it can output full canonical names directly.
    """
    from src.domain.novel.dialogue_span import is_noise_speaker
    from src.domain.novel.speaker_attributor import candidates_from_text

    max_n = max(1, int(max_n))
    text = chapter_text or ""
    out: list[str] = []

    def _add(name: str) -> bool:
        n = (name or "").strip()
        if not n or is_noise_speaker(n) or n in out:
            return False
        out.append(n)
        return True

    def _local_harvest() -> list[str]:
        """LLM chapter harvest (re-verified against window text) or regex fallback."""
        if chapter_harvest is not None:
            return [n for n in chapter_harvest if n in text]
        return []

    span_list = list(spans or [])
    if prefer_local:
        if span_list:
            for n in _high_confidence_seeds(span_list, high_min):
                _add(n)
                if len(out) >= max_n:
                    return out[:max_n]
            if chapter_harvest is not None:
                for n in _local_harvest():
                    _add(n)
                    if len(out) >= max_n:
                        return out[:max_n]
            else:
                for n in _window_local_candidates(text, span_list, max_n=max_n):
                    _add(n)
                    if len(out) >= max_n:
                        return out[:max_n]
        else:
            if chapter_harvest is not None:
                for n in _local_harvest():
                    _add(n)
                    if len(out) >= max_n:
                        return out[:max_n]
            else:
                for name in candidates_from_text(text, max_n=max_n * 2):
                    _add(name)
                    if len(out) >= max_n:
                        return out[:max_n]

    # volume seed ∩ chapter text (prior that is actually present)
    for n in volume_seed or []:
        n = (n or "").strip()
        if n and n in text:
            _add(n)
        if len(out) >= max_n:
            return out[:max_n]

    # cold-start / backfill from full volume seed order
    for n in volume_seed or []:
        _add((n or "").strip())
        if len(out) >= max_n:
            break
    return out[:max_n]


def _turns_to_blocks(
    *,
    doc_id: str,
    chapter_index: int,
    chapter_title: str,
    turns: list,
    turns_per_block: int,
    vec_max: int,
    ref_narrative_id: str,
    is_noise_speaker,
) -> list:
    from src.domain.novel.models import DialogueTurn, NovelBlock

    if not turns:
        return []
    # Normalize to DialogueTurn
    norm: list[DialogueTurn] = []
    for i, t in enumerate(turns):
        if isinstance(t, DialogueTurn):
            norm.append(t)
            continue
        if isinstance(t, dict):
            norm.append(
                DialogueTurn(
                    turn=i + 1,
                    speaker=str(t.get("speaker") or "未知"),
                    content=str(t.get("content") or ""),
                    confidence=float(t.get("confidence") or 0.0),
                )
            )
    blocks = []
    for bi in range(0, len(norm), turns_per_block):
        chunk = norm[bi : bi + turns_per_block]
        # re-number turns inside block
        for j, t in enumerate(chunk):
            t.turn = j + 1
        chunk_speakers = [
            t.speaker
            for t in chunk
            if t.speaker and t.speaker != "未知" and not is_noise_speaker(t.speaker)
        ]
        chunk_speakers = list(dict.fromkeys(chunk_speakers))
        all_d = " ".join(t.content for t in chunk)
        if len(all_d) > vec_max:
            all_d = all_d[:vec_max]
        block_index = bi // turns_per_block
        blocks.append(
            NovelBlock(
                global_id=f"{doc_id}_c{chapter_index:03d}_d{block_index:04d}",
                doc_id=doc_id,
                source=f"《{doc_id}》{chapter_title}" if chapter_title else doc_id,
                chapter_title=chapter_title or "",
                block_type="dialogue",
                dialogues=chunk,
                characters=chunk_speakers,
                vec_text_dialogue=all_d,
                all_person=chunk_speakers,
                ref_narrative_id=ref_narrative_id,
                token_length=len(all_d),
            )
        )
    return blocks


