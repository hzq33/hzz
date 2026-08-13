"""Dialogue extraction — document-level entry, provider gates, chapter-first extract.

Extracted from the former monolithic ``dialogue_pipeline.py``; logic unchanged.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from src.application.novel.dialogue_pipeline.config import _attr_config
from src.application.novel.dialogue_pipeline.legacy import _extract_legacy_window
from src.application.novel.dialogue_pipeline.models import DialoguePipelineResult
from src.application.novel.dialogue_pipeline.tools import (
    _turns_to_blocks,
    assemble_prompt_candidates,
)

logger = logging.getLogger("agent")


async def extract_dialogue_for_document(
    document: Any,
    doc_id: str,
    *,
    ref_narrative_id: str = "",
    llm_client: Any = None,
    volume_seed: Sequence[str] | None = None,
    inventory_characters: Sequence[Any] | None = None,
    config: dict | None = None,
    candidate_source: str | None = None,
) -> DialoguePipelineResult:
    """Extract dialogue blocks for a document (chapter-first or legacy)."""
    cfg = dict(config or _attr_config())
    if candidate_source:
        cfg["candidate_source"] = candidate_source
    provider = str(cfg.get("provider") or "cloud_chapter").lower()

    # Normalize aliases
    if provider in ("cloud_chapter", "chapter_first", "chapter"):
        return await _extract_chapter_first(
            document,
            doc_id,
            ref_narrative_id=ref_narrative_id,
            llm_client=llm_client,
            volume_seed=volume_seed,
            inventory_characters=inventory_characters,
            cfg=cfg,
            provider=provider,
        )
    if provider in ("legacy_window", "cloud", "haruhi_window", "off"):
        return await _extract_legacy_window(
            document,
            doc_id,
            ref_narrative_id=ref_narrative_id,
            llm_client=llm_client,
            volume_seed=volume_seed,
            cfg=cfg,
            provider=provider,
        )
    logger.warning("Unknown dialogue provider=%s; falling back to cloud_chapter", provider)
    return await _extract_chapter_first(
        document,
        doc_id,
        ref_narrative_id=ref_narrative_id,
        llm_client=llm_client,
        volume_seed=volume_seed,
        inventory_characters=inventory_characters,
        cfg=cfg,
        provider="cloud_chapter",
    )


def _provider_needs_llm(provider: str) -> bool:
    p = (provider or "").lower()
    return p in (
        "cloud_chapter",
        "chapter_first",
        "chapter",
        "cloud",
        "legacy_window",
        "haruhi_window",
    )


async def _extract_chapter_first(
    document: Any,
    doc_id: str,
    *,
    ref_narrative_id: str,
    llm_client: Any,
    volume_seed: Sequence[str] | None,
    inventory_characters: Sequence[Any] | None = None,
    cfg: dict,
    provider: str,
) -> DialoguePipelineResult:
    import asyncio

    from src.domain.novel.dialogue_chunk import dedupe_turns, plan_document_windows
    from src.domain.novel.dialogue_llm import LLMDialogueExtractor
    from src.domain.novel.dialogue_quota import (
        build_quota_tracker,
        filter_turns_for_index,
        normalize_character_name,
        order_windows_quota,
    )
    from src.domain.novel.dialogue_span import is_noise_speaker
    from src.domain.novel.dialogue_speaker_guard import sanitize_turns

    mode = str(cfg.get("mode") or "quota").lower()
    accept_min = float(cfg.get("accept_min", 0.7))
    accept_min_strict = float(cfg.get("accept_min_strict", accept_min))
    reject_vocative = bool(cfg.get("reject_vocative", True))
    max_calls = int(cfg.get("max_calls_per_doc", 80))
    max_turns_indexed = int(cfg.get("max_turns_indexed_per_doc", 1200))
    stop_when_met = bool(cfg.get("stop_when_priority_met", True))
    index_unknown = bool(cfg.get("index_unknown", False))
    turns_per_block = max(1, int(cfg.get("turns_per_block", 40)))
    vec_max = max(200, int(cfg.get("vec_text_max_chars", 1200)))
    max_out = int(cfg.get("max_output_tokens", 4096))
    concurrency = max(1, int(cfg.get("concurrency", 4)))
    quotas = cfg.get("quotas") if isinstance(cfg.get("quotas"), dict) else None
    min_cov = float(cfg.get("min_chapter_coverage_main", 0.3))
    max_win_per_ch = int(cfg.get("max_windows_per_chapter", 3))
    quote_patterns = str(cfg.get("quote_patterns") or "") or None
    max_prompt_cands = max(
        1, int(cfg.get("max_prompt_candidates") or cfg.get("max_candidates") or 10)
    )
    prefer_local = bool(cfg.get("prefer_local_over_volume_seed", True))
    high_min = float(cfg.get("high_confidence_min", 0.85))

    seed: list[str] = []
    for n in volume_seed or []:
        n = (n or "").strip()
        if n and not is_noise_speaker(n) and n not in seed:
            seed.append(n)

    chapters = list(getattr(document, "chapters", None) or [])
    windows, skipped, plan_meta = plan_document_windows(
        chapters,
        max_chunk_chars=int(cfg.get("max_chunk_chars", 6000)),
        slide_win_chars=int(cfg.get("slide_win_chars", 3500)),
        slide_stride_chars=int(cfg.get("slide_stride_chars", 2000)),
        require_quote_marks=bool(cfg.get("require_quote_marks", True)),
        min_chapter_chars=int(cfg.get("min_chapter_chars", 80)),
        skip_title_patterns=str(cfg.get("skip_title_patterns") or "") or None,
        quote_patterns=quote_patterns,
    )
    dialogue_chapter_count = plan_meta.get("chapters_total", 0) - plan_meta.get(
        "chapters_skipped", 0
    )

    if llm_client is None:
        logger.warning("cloud_chapter requested but no llm_client; returning empty dialogue")
        meta = {
            **plan_meta,
            "provider": provider,
            "mode": mode,
            "llm_calls": 0,
            "turns": 0,
            "turns_indexed": 0,
            "unknown": 0,
            "blocks": 0,
            "seed_size": len(seed),
            "dedupe_dropped": 0,
            "conflicts": 0,
            "truncated": False,
            "stopped_reason": "no_llm_client",
            "error": "no_llm_client",
        }
        return DialoguePipelineResult(blocks=[], meta=meta, volume_seed=seed)

    # ── 别名全名映射表（一次加载，传给 LLM 做归因）──
    series_id = doc_id.rsplit("__", 1)[0] if "__" in doc_id else doc_id
    from src.application.novel.dialogue_pipeline.tools import build_alias_prompt_text
    alias_map_text = build_alias_prompt_text(series_id)

    extractor = LLMDialogueExtractor(
        llm_client,
        max_tokens=max_out,
        temperature=0.0,
    )

    truncated = False
    if len(windows) > max_calls:
        windows = windows[:max_calls]
        truncated = True

    # ── LLM chapter harvest（前置，每章 1 次；失败/禁用 → None 走正则降级）──
    # 候选池来源 candidate_source：
    #   "inventory"（默认）= inventory 名单 ∩ 本章文本定位，零 LLM 调用
    #   "harvest"          = 每章 LLM 收割（旧行为）
    harvest_cfg = cfg.get("harvest") if isinstance(cfg.get("harvest"), dict) else {}
    chapter_harvest: dict[int, list[str] | None] = {}
    harvest_calls = 0
    harvest_total = 0
    candidate_source = str(cfg.get("candidate_source") or "inventory").lower()
    if candidate_source not in ("inventory", "harvest"):
        candidate_source = "inventory"

    if candidate_source == "inventory":
        # inventory-driven 候选池：inventory 全量名单（canonical+aliases 展开）∩ 本章文本
        inv_names: list[str] = []
        for c in list(inventory_characters or []):
            if isinstance(c, dict):
                cn = str(c.get("canonical_name") or "")
                al = [str(x) for x in (c.get("aliases") or []) if str(x).strip()]
            else:
                cn = str(getattr(c, "canonical_name", None) or "")
                al = [
                    str(x) for x in (getattr(c, "aliases", None) or []) if str(x).strip()
                ]
            for n in [cn, *al]:
                n = (n or "").strip()
                if n and n not in inv_names:
                    inv_names.append(n)
        if not inv_names:
            # 无 inventory 对象时回退 volume_seed（频率过滤后的名单）
            inv_names = [str(n).strip() for n in (volume_seed or []) if str(n).strip()]
        if inv_names:
            from src.domain.novel.character_inventory.llm_ner import (
                chapter_names_from_inventory,
            )

            inv_chs = sorted({int(w.chapter_index) for w in windows})
            for ch_i in inv_chs:
                ch = chapters[ch_i] if 0 <= ch_i < len(chapters) else None
                if ch is None:
                    chapter_harvest[ch_i] = None
                    continue
                names = chapter_names_from_inventory(
                    getattr(ch, "text", "") or "", inv_names
                )
                # [] = 本章无名单成员（不触发正则降级）；None 仅用于章节缺失
                chapter_harvest[ch_i] = names
                if names:
                    harvest_total += len(names)
            logger.info(
                "Candidate source=inventory: pool=%d names, chapters=%d, hit=%d",
                len(inv_names),
                len(inv_chs),
                harvest_total,
            )
    elif harvest_cfg.get("enabled", True) and llm_client is not None:
        from src.application.novel.dialogue_pipeline.harvest import (
            harvest_chapter_names,
        )

        harvest_max_names = int(harvest_cfg.get("max_names", 20))
        harvest_max_tokens = int(harvest_cfg.get("max_tokens", 512))
        harvest_chs = sorted({int(w.chapter_index) for w in windows})
        # harvest 并发独立可配（默认 4，受全局 concurrency 上限约束）
        harvest_concurrency = max(
            2, min(int(harvest_cfg.get("concurrency", 4)), max(concurrency, 4))
        )
        h_sem = asyncio.Semaphore(harvest_concurrency)

        async def _harvest_one(ch_i: int) -> tuple[int, list[str] | None]:
            async with h_sem:
                ch = chapters[ch_i] if 0 <= ch_i < len(chapters) else None
                if ch is None:
                    return ch_i, None
                return ch_i, await harvest_chapter_names(
                    getattr(ch, "text", "") or "",
                    llm_client,
                    max_names=harvest_max_names,
                    max_tokens=harvest_max_tokens,
                )

        try:
            h_results = await asyncio.gather(*[_harvest_one(i) for i in harvest_chs])
        except Exception:  # noqa: BLE001 - harvest is best-effort
            h_results = [(i, None) for i in harvest_chs]
        for ch_i, names in h_results:
            chapter_harvest[ch_i] = names
            harvest_calls += 1
            if names:
                harvest_total += len(names)

    # ── 说话活跃度统计（harvest 跨章频次）→ 重要性档位修正 ──
    # 修复：仅按 mention 排序会把"被提及多但不开口"的角色（封印中的龙等）
    # 抬进 main，而真正有台词的主角被降级。harvest 是每章说话人收割，
    # 跨章出现章数 = 说话活跃度的代理信号。
    from collections import Counter

    _speaker_chapters: Counter = Counter()
    for names in chapter_harvest.values():
        if not names:
            continue
        for n in dict.fromkeys(str(x).strip() for x in names):
            if n and not is_noise_speaker(n):
                # 与 _char_name 对齐：译名归一（利姆路→利姆露），
                # 否则 harvest 原始名无法匹配 inventory canonical。
                _speaker_chapters[normalize_character_name(n)] += 1
    speaker_scores = dict(_speaker_chapters)
    if speaker_scores:
        logger.info(
            "Speaker activity (chapters): %s",
            dict(sorted(speaker_scores.items(), key=lambda x: -x[1])[:8]),
        )

    # ── quota tracker（harvest 之后：说话活跃度参与重要性判定）──
    tracker = build_quota_tracker(
        inventory_characters,
        quotas=quotas,
        supporting_top_n=int(cfg.get("supporting_top_n", 20)),
        main_top_n=int(cfg.get("main_top_n", 5)),
        promote_importance_by_mentions=bool(
            cfg.get("promote_importance_by_mentions", True)
        ),
        volume_seed=seed,
        merge_alias_collisions_flag=bool(cfg.get("merge_alias_collisions", True)),
        merge_near_duplicates_flag=bool(cfg.get("merge_near_duplicates", True)),
        near_duplicate_max_distance=int(cfg.get("near_duplicate_max_distance", 2)),
        near_duplicate_min_len=int(cfg.get("near_duplicate_min_len", 4)),
        importance_blacklist=(
            list(cfg.get("importance_blacklist") or [])
            if isinstance(cfg.get("importance_blacklist"), (list, tuple))
            else []
        ),
        speaker_scores=speaker_scores,
    )

    # quota 模式：按 tracker 档位/配额排序窗口（必须在 tracker 构建之后）
    if mode == "quota":
        windows = order_windows_quota(
            windows,
            chapters=chapters,
            tracker=tracker,
            max_windows_per_chapter=max_win_per_ch,
        )
    # else full: keep plan order

    by_chapter: dict[int, list[dict]] = {}
    stopped_reason = "exhausted"
    sem = asyncio.Semaphore(concurrency)
    vocative_rejected = 0
    unmapped_rejected = 0
    extracted_n = 0  # LLM 有效抽取量（sanitize 后），meta["turns"] 口径

    # Process in batches so quota mode can stop early between batches
    batch_size = max(concurrency, 1)
    processed = 0
    while processed < len(windows):
        if (
            mode == "quota"
            and stop_when_met
            and tracker.priority_satisfied(
                dialogue_chapter_count=max(1, int(dialogue_chapter_count)),
                min_chapter_coverage_main=min_cov,
            )
        ):
            stopped_reason = "priority_met"
            break
        if tracker.indexed_total >= max_turns_indexed:
            stopped_reason = "max_turns"
            break

        batch = windows[processed : processed + batch_size]
        processed += len(batch)

        async def _one(w):
            async with sem:
                cands = assemble_prompt_candidates(
                    volume_seed=seed,
                    chapter_text=getattr(w, "text", "") or "",
                    spans=None,
                    max_n=max_prompt_cands,
                    high_min=high_min,
                    prefer_local=prefer_local,
                    chapter_harvest=chapter_harvest.get(int(w.chapter_index)),
                    series_id=doc_id.rsplit("__", 1)[0],
                )
                return w, await extractor.extract_window(
                    w.text,
                    chapter_title=w.chapter_title,
                    candidates=cands,
                    max_tokens=max_out,
                    alias_map_text=alias_map_text,
                )

        results = await asyncio.gather(*[_one(w) for w in batch])

        for w, turns in results:
            cleaned = sanitize_turns(
                turns,
                candidates=seed,
                accept_min_strict=accept_min_strict,
                reject_vocative=reject_vocative,
            )
            extracted_n += len(cleaned)
            for t in cleaned:
                reason = t.pop("_reject_reason", None)
                if reason == "vocative":
                    vocative_rejected += 1
                elif reason == "unmapped_low_conf":
                    unmapped_rejected += 1

            for t in cleaned:
                sp = str(t.get("speaker") or "")
                conf = float(t.get("confidence") or 0)
                if (
                    sp
                    and sp != "未知"
                    and not is_noise_speaker(sp)
                    and conf >= accept_min
                    and sp not in seed
                ):
                    seed.append(sp)

            if mode == "quota":
                filtered = filter_turns_for_index(
                    cleaned,
                    chapter_index=int(w.chapter_index),
                    tracker=tracker,
                    accept_min=accept_min,
                    index_unknown=index_unknown,
                    max_turns_indexed=max_turns_indexed,
                    is_noise_speaker=is_noise_speaker,
                )
                by_chapter.setdefault(w.chapter_index, []).extend(filtered)
            else:
                by_chapter.setdefault(w.chapter_index, []).extend(cleaned)

        if truncated and processed >= len(windows):
            stopped_reason = "max_calls"

    llm_calls = extractor.api_calls
    if stopped_reason == "exhausted" and truncated:
        stopped_reason = "max_calls"

    blocks: list = []
    total_turns = 0
    unknown_n = 0
    dedupe_dropped = 0
    conflicts = 0

    for ch_i in sorted(by_chapter.keys()):
        title = ""
        if 0 <= ch_i < len(chapters):
            title = getattr(chapters[ch_i], "title", None) or ""
        deduped = dedupe_turns(by_chapter[ch_i])
        dedupe_dropped += deduped.dropped
        conflicts += deduped.conflicts
        kept_turns = deduped.turns

        if mode == "full":
            # full: no per-character quota; optional drop unknown + hard cap
            capped: list[dict] = []
            for t in kept_turns:
                if tracker.indexed_total >= max_turns_indexed:
                    break
                sp = str(t.get("speaker") or "未知")
                if (sp == "未知" or is_noise_speaker(sp)) and not index_unknown:
                    tracker.skipped_unknown += 1
                    continue
                capped.append(t)
                tracker.indexed_total += 1
            kept_turns = capped

        total_turns += len(kept_turns)
        for t in kept_turns:
            sp = t.get("speaker") or "未知"
            if sp == "未知" or is_noise_speaker(sp):
                unknown_n += 1
        blocks.extend(
            _turns_to_blocks(
                doc_id=doc_id,
                chapter_index=ch_i,
                chapter_title=title,
                turns=kept_turns,
                turns_per_block=turns_per_block,
                vec_max=vec_max,
                ref_narrative_id=ref_narrative_id,
                is_noise_speaker=is_noise_speaker,
            )
        )

    meta = {
        **plan_meta,
        "provider": provider,
        "mode": mode,
        "llm_calls": llm_calls,
        "truncated_responses": int(getattr(extractor, "truncated_responses", 0) or 0),
        "turns": extracted_n,
        # 实际入库 = dedupe 后 kept（tracker.indexed_total 在 dedupe 前累计，含被丢重复）
        "turns_indexed": total_turns,
        "unknown": unknown_n,
        "blocks": len(blocks),
        "seed_size": len(seed),
        "max_prompt_candidates": max_prompt_cands,
        "prefer_local_over_volume_seed": prefer_local,
        "dedupe_dropped": dedupe_dropped,
        "conflicts": conflicts,
        "truncated": truncated,
        "stopped_reason": stopped_reason,
        "skipped_unknown": tracker.skipped_unknown,
        "skipped_quota_full": tracker.skipped_quota_full,
        "vocative_rejected": vocative_rejected,
        "unmapped_rejected": unmapped_rejected,
        "accept_min": accept_min,
        "per_character": tracker.snapshot(),
        "harvest_calls": harvest_calls,
        "harvest_total": harvest_total,
        "skipped_titles": [s.title for s in skipped[:20]],
        "inventory_chars": len(list(inventory_characters or [])),
        "promote_importance_by_mentions": bool(
            cfg.get("promote_importance_by_mentions", True)
        ),
        "main_top_n": int(cfg.get("main_top_n", 5)),
        "merged_alias_collisions": int(
            (tracker.diagnostics or {}).get("merged_alias_collisions") or 0
        ),
        "merged_near_duplicates": int(
            (tracker.diagnostics or {}).get("merged_near_duplicates") or 0
        ),
        "importance_blacklisted": int(
            (tracker.diagnostics or {}).get("importance_blacklisted") or 0
        ),
        "merged_alias_pairs": list(
            (tracker.diagnostics or {}).get("merged_alias_pairs") or []
        ),
    }
    # ── 说话人 LLM 校正：碎片/简称归一到权威名（防 微微一/佳树皮 等进 roster）──
    if bool(cfg.get("llm_correct_speakers", True)) and blocks and llm_client is not None:
        try:
            from src.application.novel.dialogue_pipeline.speaker_correct import (
                apply_speaker_mapping,
                correct_speakers,
            )

            raw_speakers: list[str] = []
            for block in blocks:
                for turn in getattr(block, "dialogues", None) or []:
                    sp = (getattr(turn, "speaker", None) or "").strip()
                    if sp and sp != "未知" and not is_noise_speaker(sp):
                        raw_speakers.append(sp)
            authority = [str(n) for n in (seed or [])] if seed else []
            for inv in (inventory_characters or []):
                n = getattr(inv, "canonical_name", None) or getattr(inv, "name", None)
                if n and n not in authority:
                    authority.append(str(n))
            # 权威名单缺失（inventory NER 失败）时：用对话内高频说话人兜底
            if not authority:
                from collections import Counter as _Counter

                cnt: _Counter = _Counter()
                for block in blocks:
                    for turn in getattr(block, "dialogues", None) or []:
                        sp = (getattr(turn, "speaker", None) or "").strip()
                        if sp and sp != "未知" and not is_noise_speaker(sp):
                            cnt[sp] += 1
                authority = [s for s, _ in cnt.most_common(40)]
            mapping = await correct_speakers(raw_speakers, authority, llm_client)
            if mapping:
                changed, noise = apply_speaker_mapping(
                    blocks, mapping, is_noise_speaker=is_noise_speaker
                )
                meta["speaker_corrected"] = changed
                meta["speaker_noise_marked"] = noise
        except Exception as exc:  # noqa: BLE001 - 校正失败不影响入库
            logger.warning("Speaker correction skipped: %s", exc)
    logger.info("Dialogue chapter_first done: %s", meta)
    return DialoguePipelineResult(blocks=blocks, meta=meta, volume_seed=seed)


