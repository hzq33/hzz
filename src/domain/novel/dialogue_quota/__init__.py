"""Dialogue quota — priority-based extraction quota and merge helpers.

Split from the former monolithic ``dialogue_quota.py`` into:

    models.py  CharacterQuotaTarget / QuotaTracker
    merge.py   name normalization, tier settings, collision/near-dup merge
    quota.py   build_quota_tracker
    window.py  window ordering + turn filtering

Public API is unchanged.
"""

from __future__ import annotations

from src.domain.novel.dialogue_quota.merge import (
    DEFAULT_IMPORTANCE_BLACKLIST,
    DEFAULT_QUOTAS,
    assign_importance_by_mentions,
    load_importance_tier_settings,
    merge_alias_collisions,
    merge_near_duplicates,
    name_edit_distance,
    normalize_character_name,
)
from src.domain.novel.dialogue_quota.models import (
    CharacterQuotaTarget,
    QuotaTracker,
)
from src.domain.novel.dialogue_quota.quota import build_quota_tracker
from src.domain.novel.dialogue_quota.window import (
    filter_turns_for_index,
    interleave_indices,
    order_windows_quota,
)

__all__ = [
    "CharacterQuotaTarget",
    "QuotaTracker",
    "normalize_character_name",
    "load_importance_tier_settings",
    "name_edit_distance",
    "merge_alias_collisions",
    "merge_near_duplicates",
    "assign_importance_by_mentions",
    "build_quota_tracker",
    "interleave_indices",
    "order_windows_quota",
    "filter_turns_for_index",
]
