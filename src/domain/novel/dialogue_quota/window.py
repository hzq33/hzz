"""Dialogue window ordering and turn filtering.

Extracted from the former monolithic ``dialogue_quota.py``; logic unchanged.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Sequence
from typing import Any

from src.domain.novel.dialogue_quota.merge import DEFAULT_QUOTAS
from src.domain.novel.dialogue_quota.models import CharacterQuotaTarget, QuotaTracker

logger = logging.getLogger("agent")


def interleave_indices(n: int) -> list[int]:
    """Head/mid/tail interleaved order for n chapters (0..n-1 in quote-chapter list order)."""
    if n <= 0:
        return []
    if n == 1:
        return [0]
    head, mid, tail = [], [], []
    for i in range(n):
        third = n / 3.0
        if i < third:
            head.append(i)
        elif i < 2 * third:
            mid.append(i)
        else:
            tail.append(i)
    out: list[int] = []
    buckets = [head, mid, tail]
    # Round-robin pop from front of each non-empty bucket
    while any(buckets):
        for b in buckets:
            if b:
                out.append(b.pop(0))
    return out


def order_windows_quota(
    windows: Sequence[Any],
    *,
    chapters: Sequence[Any],
    tracker: QuotaTracker,
    max_windows_per_chapter: int = 2,
) -> list[Any]:
    """Reorder windows: interleave chapters, cap per-chapter windows, deficit chapters first later."""
    # Group windows by chapter
    by_ch: dict[int, list] = defaultdict(list)
    for w in windows:
        by_ch[int(w.chapter_index)].append(w)

    # Cap windows per chapter (keep first N — usually whole-chapter or first slides)
    for ch_i, wins in by_ch.items():
        wins.sort(key=lambda w: (getattr(w, "window_index", 0), getattr(w, "start", 0)))
        by_ch[ch_i] = wins[: max(1, max_windows_per_chapter)]

    ch_indices = sorted(by_ch.keys())
    # Map to dense 0..n-1 for interleave, then back
    order_pos = interleave_indices(len(ch_indices))
    ordered_chs = [ch_indices[p] for p in order_pos]

    # Promote chapters that mention deficit main names
    deficit = tracker.deficit_mains()
    if deficit and chapters:
        promoted: list[int] = []
        rest: list[int] = []
        name_sets = []
        for d in deficit:
            t = tracker.targets.get(d)
            if t:
                name_sets.append(t.name_set)
        for ch_i in ordered_chs:
            text = ""
            if 0 <= ch_i < len(chapters):
                text = getattr(chapters[ch_i], "text", None) or ""
            hit = any(any(n in text for n in ns) for ns in name_sets) if name_sets else False
            if hit:
                promoted.append(ch_i)
            else:
                rest.append(ch_i)
        # Keep interleave among promoted, then rest
        ordered_chs = promoted + rest

    out: list = []
    for ch_i in ordered_chs:
        out.extend(by_ch[ch_i])
    return out


def filter_turns_for_index(
    turns: Sequence[dict],
    *,
    chapter_index: int,
    tracker: QuotaTracker,
    accept_min: float,
    index_unknown: bool,
    max_turns_indexed: int,
    is_noise_speaker,
) -> list[dict]:
    """Accept turns under quota; update tracker. Drop unknown unless index_unknown."""
    kept: list[dict] = []
    for raw in turns:
        if tracker.indexed_total >= max_turns_indexed:
            break
        if not isinstance(raw, dict):
            continue
        speaker = str(raw.get("speaker") or "未知").strip() or "未知"
        conf = float(raw.get("confidence") or 0.0)
        content = str(raw.get("content") or "").strip()
        if not content:
            continue
        if conf < accept_min and speaker != "未知":
            # still count low-conf as unknown-ish for indexing skip
            if not index_unknown:
                tracker.skipped_unknown += 1
                continue
        if is_noise_speaker(speaker) or speaker == "未知":
            if not index_unknown:
                tracker.skipped_unknown += 1
                continue
            kept.append(raw)
            tracker.indexed_total += 1
            continue

        canon = tracker.resolve(speaker)
        if canon is None:
            # New name → treat as extra with tiny quota via dynamic target
            if speaker not in tracker.targets:
                tracker.targets[speaker] = CharacterQuotaTarget(
                    canonical=speaker,
                    importance="extra",
                    target=DEFAULT_QUOTAS["extra"],
                )
                tracker.alias_to_canonical[speaker] = speaker
            canon = speaker

        if tracker.remaining(canon) <= 0:
            tracker.skipped_quota_full += 1
            continue
        # 入库 speaker 归一为 canonical（设计: docs/ALIAS_UNIFICATION_DESIGN.md A 层）
        kept.append({**raw, "speaker": canon})
        tracker.record(canon, chapter_index)
    return kept
