"""Quota tracker construction.

Extracted from the former monolithic ``dialogue_quota.py``; logic unchanged.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from src.domain.novel.dialogue_quota.models import CharacterQuotaTarget, QuotaTracker
from src.domain.novel.dialogue_quota.merge import (
    DEFAULT_QUOTAS,
    _char_aliases,
    _char_mentions,
    _char_name,
    _set_char_importance,
    assign_importance_by_mentions,
    merge_alias_collisions,
    merge_near_duplicates,
)

logger = logging.getLogger("agent")


def build_quota_tracker(
    inventory_characters: Sequence[Any] | None,
    *,
    quotas: dict[str, int] | None = None,
    supporting_top_n: int = 20,
    main_top_n: int = 5,
    promote_importance_by_mentions: bool = True,
    volume_seed: Sequence[str] | None = None,
    merge_alias_collisions_flag: bool = True,
    merge_near_duplicates_flag: bool = True,
    near_duplicate_max_distance: int = 2,
    near_duplicate_min_len: int = 4,
    importance_blacklist: Sequence[str] | None = None,
    speaker_scores: dict[str, int] | None = None,
) -> QuotaTracker:
    """Build tracker from InventoryResult.characters or plain dicts.

    ``speaker_scores``（可选）：名字 → 说话活跃章数，用于修正重要性档位
    （见 assign_importance_by_mentions），避免"被提及多但不开口"的角色
    占用 main 档、主角被降级。
    """
    q = {**DEFAULT_QUOTAS, **(quotas or {})}
    tracker = QuotaTracker()
    bl = [str(x).strip() for x in (importance_blacklist or []) if str(x).strip()]

    chars: list[Any] = list(inventory_characters or [])
    merged_log: list[dict[str, str]] = []
    near_log: list[dict[str, str]] = []
    if merge_alias_collisions_flag and chars:
        chars, merged_log = merge_alias_collisions(chars)
    if merge_near_duplicates_flag and chars:
        chars, near_log = merge_near_duplicates(
            chars,
            max_distance=near_duplicate_max_distance,
            min_len=near_duplicate_min_len,
        )

    if promote_importance_by_mentions and chars:
        chars = assign_importance_by_mentions(
            chars,
            main_top_n=main_top_n,
            supporting_top_n=supporting_top_n,
            blacklist=bl,
            speaker_scores=speaker_scores,
        )
    else:
        chars = sorted(chars, key=_char_mentions, reverse=True)
        # Still demote blacklist when promote is off
        for c in chars:
            if _char_name(c) in set(bl):
                _set_char_importance(c, "extra")

    blacklisted_n = sum(1 for c in chars if _char_name(c) in set(bl))
    all_pairs = (merged_log + near_log)[:20]
    tracker.diagnostics = {
        "merged_alias_collisions": len(merged_log),
        "merged_near_duplicates": len(near_log),
        "merged_alias_pairs": all_pairs,
        "importance_blacklisted": blacklisted_n,
    }

    supporting_seen = 0
    for c in chars:
        name = _char_name(c)
        aliases = _char_aliases(c)
        if isinstance(c, dict):
            importance = str(c.get("importance") or "supporting")
        else:
            importance = str(getattr(c, "importance", None) or "supporting")
        if not name:
            continue
        if importance not in ("main", "supporting", "extra"):
            importance = "supporting"
        # Mention promotion already assigned tiers; only demote when promotion is off.
        if not promote_importance_by_mentions and importance == "supporting":
            supporting_seen += 1
            if supporting_seen > supporting_top_n:
                importance = "extra"
        if name in set(bl):
            importance = "extra"
        target = int(q.get(importance, q["extra"]))
        t = CharacterQuotaTarget(
            canonical=name,
            aliases=[a for a in aliases if a and a != name],
            importance=importance,
            target=target,
        )
        tracker.targets[name] = t
        tracker.alias_to_canonical[name] = name
        for a in t.aliases:
            tracker.alias_to_canonical[a] = name

    # Seed-only names not in inventory → tier by seed order when inventory missing
    seed_only = []
    for n in volume_seed or []:
        n = (n or "").strip()
        if not n or n in tracker.alias_to_canonical:
            continue
        seed_only.append(n)

    bl_set = set(bl)
    if not chars and seed_only and promote_importance_by_mentions:
        # No inventory objects: use seed order (already mention-sorted) as proxy.
        eligible_seed = [n for n in seed_only if n not in bl_set]
        blocked_seed = [n for n in seed_only if n in bl_set]
        for i, n in enumerate(eligible_seed):
            if i < main_top_n:
                imp, target = "main", int(q.get("main", q["extra"]))
            elif i < main_top_n + supporting_top_n:
                imp, target = "supporting", int(q.get("supporting", q["extra"]))
            else:
                imp, target = "extra", int(q["extra"])
            tracker.targets[n] = CharacterQuotaTarget(
                canonical=n, aliases=[], importance=imp, target=target
            )
            tracker.alias_to_canonical[n] = n
        for n in blocked_seed:
            tracker.targets[n] = CharacterQuotaTarget(
                canonical=n, aliases=[], importance="extra", target=int(q["extra"])
            )
            tracker.alias_to_canonical[n] = n
        tracker.diagnostics["importance_blacklisted"] = len(blocked_seed)
    else:
        for n in seed_only:
            imp = "extra"
            tracker.targets[n] = CharacterQuotaTarget(
                canonical=n, aliases=[], importance=imp, target=int(q["extra"])
            )
            tracker.alias_to_canonical[n] = n

    return tracker


