"""Dialogue deepening from store — enrich thin dialogue blocks.

Extracted from the former monolithic ``dialogue_pipeline.py``; logic unchanged.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from src.application.novel.dialogue_pipeline.config import _attr_config
from src.application.novel.dialogue_pipeline.models import DialoguePipelineResult
from src.application.novel.dialogue_pipeline.tools import _turns_to_blocks

logger = logging.getLogger("agent")


async def deepen_dialogue_from_store(
    store,
    *,
    canonical_name: str,
    aliases: Sequence[str] | None = None,
    doc_id: str | None = None,
    llm_client: Any = None,
    max_calls: int = 8,
    config: dict | None = None,
) -> DialoguePipelineResult:
    """L3: pull narrative hits mentioning the character and re-extract dialogue.

    Used when build/gather finds too few dialogue samples after quota ingest.
    Indexes new dialogue blocks into ``store`` when provided.
    """
    from src.domain.novel.dialogue_llm import LLMDialogueExtractor
    from src.domain.novel.dialogue_span import is_noise_speaker
    from src.domain.novel.dialogue_speaker_guard import sanitize_turns

    cfg = dict(config or _attr_config())
    name_set = {canonical_name, *[a for a in (aliases or []) if a]}
    name_set = {n for n in name_set if len(n) >= 2}
    seed = [canonical_name, *[a for a in (aliases or []) if a and not is_noise_speaker(a)]]
    max_out = int(cfg.get("max_output_tokens", 4096))
    accept_min = float(cfg.get("accept_min", 0.7))
    accept_min_strict = float(cfg.get("accept_min_strict", accept_min))
    reject_vocative = bool(cfg.get("reject_vocative", True))
    turns_per_block = max(1, int(cfg.get("turns_per_block", 40)))
    vec_max = max(200, int(cfg.get("vec_text_max_chars", 1200)))

    if llm_client is None:
        return DialoguePipelineResult(
            blocks=[],
            meta={"error": "no_llm_client", "deepen": True},
            volume_seed=seed,
        )

    query = " ".join([canonical_name, *list(aliases or [])[:3]])
    try:
        hits = await store.search(
            query, channel="narrative", doc_id=doc_id, top_k=max_calls * 2
        )
    except Exception as e:
        logger.warning("deepen narrative search failed: %s", e)
        hits = []

    windows_text: list[tuple[str, str, str]] = []  # title, text, doc_id
    seen = set()
    for h in hits:
        block = h.block
        text = (getattr(block, "narrative_text", None) or "").strip()
        if not text or not any(n in text for n in name_set):
            continue
        if not any(q in text for q in ("「", "」", "『", "』", "“", "”", '"')):
            continue
        gid = getattr(block, "global_id", "") or text[:40]
        if gid in seen:
            continue
        seen.add(gid)
        windows_text.append(
            (
                getattr(block, "chapter_title", "") or "",
                text[:3500],
                getattr(block, "doc_id", "") or doc_id or "deepen",
            )
        )
        if len(windows_text) >= max_calls:
            break

    extractor = LLMDialogueExtractor(llm_client, max_tokens=max_out, temperature=0.0)
    all_turns: list[dict] = []
    for title, text, _did in windows_text:
        turns = await extractor.extract_window(
            text, chapter_title=title, candidates=seed, max_tokens=max_out
        )
        cleaned = sanitize_turns(
            turns,
            candidates=seed,
            accept_min_strict=accept_min_strict,
            reject_vocative=reject_vocative,
        )
        for t in cleaned:
            t.pop("_reject_reason", None)
            sp = str(t.get("speaker") or "")
            if is_noise_speaker(sp) or sp == "未知":
                continue
            if float(t.get("confidence") or 0) < accept_min:
                continue
            # Keep turns that match target character
            if not any(
                sp == n or (len(n) >= 2 and (n in sp or sp in n)) for n in name_set
            ):
                continue
            all_turns.append(t)

    from src.domain.novel.dialogue_chunk import dedupe_turns

    deduped = dedupe_turns(all_turns)
    use_doc = doc_id or (windows_text[0][2] if windows_text else "deepen")
    blocks = _turns_to_blocks(
        doc_id=use_doc,
        chapter_index=0,
        chapter_title=f"deepen:{canonical_name}",
        turns=deduped.turns,
        turns_per_block=turns_per_block,
        vec_max=vec_max,
        ref_narrative_id="",
        is_noise_speaker=is_noise_speaker,
    )
    # Stable deepen ids
    for i, b in enumerate(blocks):
        b.global_id = f"{use_doc}_deepen_{canonical_name}_{i:04d}"

    indexed = 0
    if blocks and store is not None:
        try:
            if hasattr(store, "index_batch"):
                indexed = await store.index_batch(blocks)
            else:
                for b in blocks:
                    await store.index(b)
                    indexed += 1
        except Exception as e:
            logger.warning("deepen index failed: %s", e)


    meta = {
        "deepen": True,
        "canonical_name": canonical_name,
        "windows": len(windows_text),
        "llm_calls": extractor.api_calls,
        "turns": len(deduped.turns),
        "blocks": len(blocks),
        "indexed": indexed,
    }
    logger.info("Dialogue deepen done: %s", meta)
    return DialoguePipelineResult(blocks=blocks, meta=meta, volume_seed=seed)


