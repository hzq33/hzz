"""Legacy window extraction fallback.

Extracted from the former monolithic ``dialogue_pipeline.py``; logic unchanged.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from src.application.novel.dialogue_pipeline.models import DialoguePipelineResult
from src.application.novel.dialogue_pipeline.tools import (
    _high_confidence_seeds,
    _turns_to_blocks,
    _window_local_candidates,
)

logger = logging.getLogger("agent")


async def _extract_legacy_window(
    document: Any,
    doc_id: str,
    *,
    ref_narrative_id: str,
    llm_client: Any,
    volume_seed: Sequence[str] | None,
    cfg: dict,
    provider: str,
) -> DialoguePipelineResult:
    """Regex spans + short-window speaker attribution (legacy)."""
    from src.domain.novel.dialogue_span import extract_spans, is_noise_speaker
    from src.domain.novel.models import DialogueTurn
    from src.domain.novel.speaker_attributor import (
        CloudSpeakerAttributor,
        apply_attribution,
    )
    from src.domain.novel.speaker_window import build_windows

    high_min = float(cfg.get("high_confidence_min", 0.85))
    accept_min = float(cfg.get("accept_min", 0.5))
    batch_size = int(cfg.get("batch_size", 5))
    context_sentences = int(cfg.get("context_sentences", 2))
    max_candidates = int(cfg.get("max_candidates", 12))
    max_calls = int(cfg.get("max_calls_per_doc", 500))
    cold_start = bool(cfg.get("cold_start_open_extract", True))
    turns_per_block = max(1, int(cfg.get("turns_per_block", 40)))
    vec_max = max(200, int(cfg.get("vec_text_max_chars", 1200)))
    concurrency = max(1, int(cfg.get("concurrency", 8)))

    seed: list[str] = []
    for n in volume_seed or []:
        n = (n or "").strip()
        if n and not is_noise_speaker(n) and n not in seed:
            seed.append(n)

    attributor = None
    if provider in ("cloud", "legacy_window"):
        if llm_client is None:
            logger.warning(
                "dialogue attribution provider=%s but no llm_client; using rules only",
                provider,
            )
            provider = "off"
        else:
            max_tokens = max(256, 120 * batch_size + 64)
            attributor = CloudSpeakerAttributor(
                llm_client,
                temperature=0.0,
                max_tokens=max_tokens,
                concurrency=concurrency,
            )
    elif provider == "haruhi_window":
        try:
            from src.domain.novel.dialogue_local_llm import LocalLLMDialogueExtractor
            from src.domain.novel.speaker_attributor import HaruhiWindowAttributor

            extractor = LocalLLMDialogueExtractor(mode="single")
            extractor.load()
            attributor = HaruhiWindowAttributor(extractor)
        except Exception as e:
            logger.warning("Haruhi window attributor unavailable: %s; rules only", e)
            provider = "off"

    blocks: list = []
    total_spans = 0
    high_n = 0
    attributed_n = 0
    unknown_n = 0
    llm_calls = 0
    truncated = False

    for chapter_index, chapter in enumerate(getattr(document, "chapters", None) or []):
        text = chapter.text or ""
        if not text.strip():
            continue
        span_res = extract_spans(
            text,
            high_confidence_min=high_min,
            speaker_mode="high_only",
        )
        spans = span_res.spans
        if not spans:
            continue
        total_spans += len(spans)

        chapter_seeds = _high_confidence_seeds(spans, high_min)
        for n in chapter_seeds:
            if n not in seed:
                seed.append(n)
        high_n += sum(1 for s in spans if not s.needs_attribution)

        extra = list(seed)
        if cold_start and len([x for x in extra if x != "未知"]) < 2:
            for n in _window_local_candidates(text, spans, max_n=6):
                if n not in extra:
                    extra.append(n)

        need = [s for s in spans if s.needs_attribution]
        results = []
        if need and attributor is not None and provider != "off":
            windows = build_windows(
                text,
                spans,
                batch_size=batch_size,
                context_sentences=context_sentences,
                max_candidates=max_candidates,
                chapter_title=chapter.title or "",
                extra_candidates=extra,
            )
            if llm_calls + len(windows) > max_calls:
                keep = max(0, max_calls - llm_calls)
                windows = windows[:keep]
                truncated = True
            if windows:
                will_call = sum(
                    1
                    for w in windows
                    if any(c and c != "未知" for c in (w.candidate_speakers or []))
                )
                batch_results = await attributor.attribute(windows)
                results.extend(batch_results)
                llm_calls += will_call
                attributed_n += sum(
                    1
                    for r in batch_results
                    if r.said_by != "未知" and r.confidence >= accept_min
                )

        turn_dicts = apply_attribution(spans, results, accept_min=accept_min)
        turns: list[DialogueTurn] = []
        for i, td in enumerate(turn_dicts):
            sp = td["speaker"]
            conf = float(td.get("confidence") or 0.0)
            if sp == "未知" or is_noise_speaker(sp):
                unknown_n += 1
            elif conf >= accept_min and sp not in seed:
                seed.append(sp)
            turns.append(
                DialogueTurn(
                    turn=i + 1,
                    speaker=sp,
                    content=td["content"],
                    confidence=conf,
                )
            )

        if not turns:
            continue

        blocks.extend(
            _turns_to_blocks(
                doc_id=doc_id,
                chapter_index=chapter_index,
                chapter_title=chapter.title or "",
                turns=turns,
                turns_per_block=turns_per_block,
                vec_max=vec_max,
                ref_narrative_id=ref_narrative_id,
                is_noise_speaker=is_noise_speaker,
            )
        )

    meta = {
        "spans": total_spans,
        "high_confidence": high_n,
        "attributed": attributed_n,
        "unknown": unknown_n,
        "llm_calls": llm_calls,
        "truncated": truncated,
        "provider": provider,
        "blocks": len(blocks),
        "seed_size": len(seed),
        "mode": "legacy_window",
    }
    logger.info("Dialogue legacy_window done: %s", meta)
    return DialoguePipelineResult(blocks=blocks, meta=meta, volume_seed=seed)
