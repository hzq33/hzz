"""Inventory → roster persistence.

Extracted from the former monolithic ``character_inventory.py``; logic unchanged.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from src.domain.novel.character_inventory.models import InventoryResult

logger = logging.getLogger("agent")


def persist_inventory_roster(
    *,
    series_id: str,
    doc_id: str,
    inventory: InventoryResult,
    dialogue_blocks: list | None = None,
) -> Any:
    """Write L1 roster from inventory; optionally attach dialogue counts.

    alias.json is owned by Phase 3a2 (_persist_alias_json); this function
    only writes CharacterRoster and does NOT touch alias.json.
    """
    from src.domain.novel.character_roster import (
        CharacterRoster,
        RosterEntry,
        character_id_for,
        load_roster,
        save_roster,
    )
    from src.domain.novel.dialogue_span import is_noise_speaker

    # Optional dialogue frequency by name/alias
    dlg_counts: dict[str, int] = {}
    if dialogue_blocks:
        for block in dialogue_blocks:
            for turn in getattr(block, "dialogues", None) or []:
                sp = (getattr(turn, "speaker", None) or "").strip()
                if not sp or is_noise_speaker(sp) or sp == "未知":
                    continue
                dlg_counts[sp] = dlg_counts.get(sp, 0) + 1

    existing = load_roster(series_id) or CharacterRoster(series_id=series_id, doc_ids=[])
    if doc_id and doc_id not in existing.doc_ids:
        existing.doc_ids.append(doc_id)
    prev = {e.name: e for e in existing.characters}

    new_entries: list[RosterEntry] = []
    for c in inventory.characters:
        cid = character_id_for(series_id, c.canonical_name)
        # dialogue count: sum matches on canonical + aliases
        dc = 0
        for n in [c.canonical_name, *c.aliases]:
            dc += dlg_counts.get(n, 0)
        old = prev.get(c.canonical_name)
        entry = RosterEntry(
            name=c.canonical_name,
            aliases_observed=sorted(set(c.aliases) | set(old.aliases_observed if old else [])),
            dialogue_count=max(dc, old.dialogue_count if old else 0, 0),
            mention_count=max(c.mention_count, old.mention_count if old else 0),
            chapters=list(old.chapters) if old else [],
            co_occurrence=dict(old.co_occurrence) if old else {},
            status=old.status if old else "candidate",
            character_id=old.character_id if old else cid,
            has_card=old.has_card if old else False,
        )
        new_entries.append(entry)

    # Keep previous entries from other volumes not in this inventory pass.
    # 但旧条目若不在新 inventory 名单、且非新名单别名或已是某角色别名 → 移除
    #（NER 失败时 fallback 的碎片 微微一/佳树皮 等，以及已归一化的简称别名条目）。
    seen = {e.name for e in new_entries}
    protected = {e.name for e in new_entries if getattr(e, "has_card", False)}
    new_authority = {c.canonical_name for c in inventory.characters}
    new_aliases = set()
    for c in inventory.characters:
        new_aliases.update(c.aliases or [])
    for old in existing.characters:
        if old.name in seen:
            continue
        if getattr(old, "has_card", False) or old.name in protected:
            new_entries.append(old)
            continue
        # 旧名在权威名单或其别名 → 保留（可能来自其他卷）；否则移除（碎片/废弃）
        if old.name in new_authority or old.name in new_aliases:
            new_entries.append(old)
        else:
            logger.info(
                "Roster prune: dropping stale entry %r (not in inventory)",
                old.name,
            )

    # 别名条目收敛：某条目名是新名单中另一条目的别名 → 合并掉（别名不独立成行）
    alias_to_canonical: dict[str, str] = {}
    for c in inventory.characters:
        for a in (c.aliases or []):
            alias_to_canonical.setdefault(str(a), c.canonical_name)
    if alias_to_canonical:
        pruned: list[RosterEntry] = []
        for e in new_entries:
            target = alias_to_canonical.get(e.name)
            if target and target != e.name:
                # 合并到主条目：累加对话数/提及数
                for main in pruned:
                    if main.name == target:
                        main.dialogue_count = max(main.dialogue_count, e.dialogue_count)
                        main.mention_count = max(main.mention_count, e.mention_count)
                        if e.name not in main.aliases_observed:
                            main.aliases_observed = sorted(
                                set(main.aliases_observed) | {e.name}
                            )
                        break
                logger.info(
                    "Roster alias-collapse: %r → %r", e.name, target,
                )
                continue
            pruned.append(e)
        new_entries = pruned

    # Re-inject characters that exist in inventory candidates JSON but are
    # missing from both this volume's InventoryResult and the existing roster.
    # This can happen when delete_character API removes a roster entry but
    # leaves inventory candidates intact, or when validate_by_llm drops a
    # character that was previously kept. Inventory candidates is the NER
    # source of truth — use it to heal the roster.
    try:
        from src.domain.novel.character_inventory.candidates import (
            load_inventory_candidates,
        )

        inv_json = load_inventory_candidates(series_id) or {}
        roster_seen = {e.name for e in new_entries}
        # Build all known names (canonical + aliases) to skip duplicate canonicals
        # (e.g. inventory has "樱井" and "樱井弘人" as separate candidates,
        # but roster already merged them under "樱井弘人" with "樱井" as alias).
        all_known: set[str] = set()
        for e in new_entries:
            all_known.add(e.name)
            all_known.update(e.aliases_observed)
        for c in inv_json.get("candidates", []):
            name = str(c.get("name") or "").strip()
            if not name or name in roster_seen:
                continue
            # Skip if name is already an alias of an existing entry
            if name in all_known:
                continue
            new_entries.append(
                RosterEntry(
                    name=name,
                    aliases_observed=list(c.get("aliases") or []),
                    dialogue_count=0,
                    mention_count=int(c.get("mention_count") or 0),
                    chapters=[],
                    co_occurrence={},
                    status="candidate",
                    character_id=character_id_for(series_id, name),
                    has_card=False,
                )
            )
            logger.info(
                "Roster healed from inventory candidates: + %s (series=%s)",
                name, series_id,
            )
    except Exception as e:
        logger.debug("Roster heal from inventory candidates skipped: %s", e)

    new_entries.sort(key=lambda e: (-e.dialogue_count, -e.mention_count, e.name))
    existing.characters = new_entries
    existing.updated_at = datetime.now(UTC).isoformat()
    save_roster(existing)
    return existing
