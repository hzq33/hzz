"""Character inventory — CLUENER + LLM normalize, candidates, roster.

Split from the former monolithic ``character_inventory.py`` into:

    models.py     InventoryCharacter / InventoryResult
    candidates.py config, seed filtering, candidate persistence, quota merge
    builder.py    document scan, cluster, LLM normalize
    roster.py     inventory → roster persistence

Public API is unchanged.
"""

from __future__ import annotations

from src.domain.novel.character_inventory.builder import (
    _fallback_from_clusters,
    _llm_normalize_global,
    _parse_llm_json,
    build_character_inventory,
)
from src.domain.novel.character_inventory.candidates import (
    SeedBuildResult,
    _inventory_config,
    as_inventory_character,
    inventory_config,
    build_llm_seed,
    filter_seed_characters,
    load_inventory_candidates,
    load_series_degree_map,
    median_mention_threshold,
    merge_volume_and_series_for_quota,
    normalize_character_name,
    percentile_mention_threshold,
    persist_inventory_candidates,
    persist_relations,
    resolve_seed_min_mentions,
    seed_names_from_inventory,
    inventory_path,
)
from src.domain.novel.character_inventory.models import (
    InventoryCharacter,
    InventoryResult,
)
from src.domain.novel.character_inventory.roster import persist_inventory_roster

__all__ = [
    "InventoryCharacter",
    "InventoryResult",
    "build_character_inventory",
    "as_inventory_character",
    "inventory_config",
    "load_inventory_candidates",
    "persist_inventory_candidates",
    "persist_relations",
    "persist_inventory_roster",
    "inventory_path",
    "median_mention_threshold",
    "percentile_mention_threshold",
    "resolve_seed_min_mentions",
    "filter_seed_characters",
    "build_llm_seed",
    "load_series_degree_map",
    "SeedBuildResult",
    "normalize_character_name",
    "merge_volume_and_series_for_quota",
    "seed_names_from_inventory",
    "_inventory_config",
]
