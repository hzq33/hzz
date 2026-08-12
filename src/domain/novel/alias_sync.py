"""Sync Alias Roster edits into CharacterRoster / inventory / character cards."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger("agent")


def detect_canonical_renames(
    old_entities: list[dict[str, Any]] | None,
    new_entities: list[dict[str, Any]] | None,
) -> list[tuple[str, str]]:
    """Detect in-place canonical renames (same row index, different name).

    Alias Roster UI edits by index, so zip-by-index is the primary signal.
    Falls back to character_id matching when present.
    """
    old_list = list(old_entities or [])
    new_list = list(new_entities or [])
    renames: list[tuple[str, str]] = []
    seen_old: set[str] = set()

    # Prefer character_id when both sides carry it
    new_by_cid: dict[str, str] = {}
    for ent in new_list:
        cid = str(ent.get("character_id") or "").strip()
        name = str(ent.get("canonical_name") or "").strip()
        if cid and name:
            new_by_cid[cid] = name
    for ent in old_list:
        cid = str(ent.get("character_id") or "").strip()
        old_name = str(ent.get("canonical_name") or "").strip()
        if not cid or not old_name or cid not in new_by_cid:
            continue
        new_name = new_by_cid[cid]
        if new_name != old_name:
            renames.append((old_name, new_name))
            seen_old.add(old_name)

    # Index-aligned renames (UI edits)
    for old_ent, new_ent in zip(old_list, new_list):
        old_name = str(old_ent.get("canonical_name") or "").strip()
        new_name = str(new_ent.get("canonical_name") or "").strip()
        if not old_name or not new_name or old_name == new_name:
            continue
        if old_name in seen_old:
            continue
        # Skip if new_name already existed as another row's canonical
        old_names = {
            str(e.get("canonical_name") or "").strip() for e in old_list
        }
        if new_name in old_names and new_name != old_name:
            # Likely swap / collision — still treat as rename of this row
            pass
        renames.append((old_name, new_name))
        seen_old.add(old_name)

    # De-dupe preserving order
    out: list[tuple[str, str]] = []
    seen_pair: set[tuple[str, str]] = set()
    for pair in renames:
        if pair not in seen_pair and pair[0] != pair[1]:
            seen_pair.add(pair)
            out.append(pair)
    return out


def apply_canonical_rename(series_id: str, old_name: str, new_name: str) -> dict[str, Any]:
    """Rename a character across roster, inventory, and card files."""
    sid = (series_id or "").strip()
    old_name = (old_name or "").strip()
    new_name = (new_name or "").strip()
    stats: dict[str, Any] = {
        "series_id": sid,
        "old_name": old_name,
        "new_name": new_name,
        "roster": False,
        "inventory": False,
        "card": False,
    }
    if not sid or not old_name or not new_name or old_name == new_name:
        return stats

    from src.domain.novel.character_roster import (
        character_id_for,
        load_roster,
        save_roster,
    )

    old_cid = character_id_for(sid, old_name)
    new_cid = character_id_for(sid, new_name)

    # ── CharacterRoster (L1) ─────────────────────────────────
    try:
        roster = load_roster(sid)
        if roster:
            changed = False
            for entry in roster.characters:
                if entry.name != old_name:
                    continue
                entry.name = new_name
                aliases = set(entry.aliases_observed or [])
                aliases.add(old_name)
                aliases.discard(new_name)
                entry.aliases_observed = sorted(aliases)
                if not entry.character_id or entry.character_id == old_cid:
                    entry.character_id = new_cid
                # Rewrite co-occurrence keys that point at old name
                fixed_co: dict[str, int] = {}
                for other, cnt in (entry.co_occurrence or {}).items():
                    key = new_name if other == old_name else other
                    fixed_co[key] = int(cnt)
                entry.co_occurrence = fixed_co
                changed = True
            for entry in roster.characters:
                if old_name in (entry.co_occurrence or {}):
                    entry.co_occurrence[new_name] = int(
                        entry.co_occurrence.pop(old_name, 0)
                    ) + int(entry.co_occurrence.get(new_name) or 0)
                    changed = True
            if changed:
                save_roster(roster)
                stats["roster"] = True
    except Exception as exc:
        logger.warning("alias rename: roster failed %s→%s: %s", old_name, new_name, exc)

    # ── Inventory candidates ─────────────────────────────────
    try:
        from src.domain.novel.character_inventory.candidates import (
            inventory_path,
            load_inventory_candidates,
        )

        data = load_inventory_candidates(sid)
        if data:
            changed = False
            for cand in data.get("candidates") or []:
                if str(cand.get("name") or "").strip() != old_name:
                    continue
                cand["name"] = new_name
                aliases = set(cand.get("aliases") or [])
                aliases.add(old_name)
                aliases.discard(new_name)
                cand["aliases"] = sorted(aliases)
                changed = True
            seeds = list(data.get("seed_names") or [])
            if old_name in seeds:
                data["seed_names"] = [
                    new_name if n == old_name else n for n in seeds
                ]
                changed = True
            if changed:
                data["updated_at"] = datetime.now(UTC).isoformat()
                path = inventory_path(sid)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                stats["inventory"] = True
    except Exception as exc:
        logger.warning(
            "alias rename: inventory failed %s→%s: %s", old_name, new_name, exc
        )

    # ── Character card ───────────────────────────────────────
    try:
        from src.domain.character_card import CharacterCard

        card = CharacterCard.load_for_series(
            sid, old_name, character_id=old_cid
        )
        if card is None:
            card = CharacterCard.load_for_series(sid, old_name)
        if card is not None:
            card.name = new_name
            card.series_id = sid or card.series_id
            card.character_id = new_cid
            card.source_work = card.source_work or sid
            aliases = set(card.aliases or [])
            aliases.add(old_name)
            aliases.discard(new_name)
            card.aliases = sorted(aliases)
            CharacterCard.save_for_series(
                sid, new_name, card, character_id=new_cid
            )
            cache_dir = CharacterCard._CACHE_DIR
            for path in (
                CharacterCard.cache_path_for(sid, old_name, character_id=old_cid),
                CharacterCard.cache_path_for(sid, old_name),
                cache_dir / f"{old_name}.json",
                cache_dir / f"{old_cid}.json",
            ):
                try:
                    if path.exists() and path.resolve() != CharacterCard.cache_path_for(
                        sid, new_name, character_id=new_cid
                    ).resolve():
                        path.unlink()
                except OSError:
                    pass
            stats["card"] = True
    except Exception as exc:
        logger.warning("alias rename: card failed %s→%s: %s", old_name, new_name, exc)

    logger.info(
        "Alias canonical rename series=%s %s→%s roster=%s inv=%s card=%s",
        sid,
        old_name,
        new_name,
        stats["roster"],
        stats["inventory"],
        stats["card"],
    )
    return stats


def sync_alias_roster_save(
    series_id: str,
    old_data: dict[str, Any] | None,
    new_data: dict[str, Any],
) -> list[dict[str, Any]]:
    """After Alias Roster save: propagate canonical renames to cards/roster."""
    renames = detect_canonical_renames(
        (old_data or {}).get("entities") if isinstance(old_data, dict) else None,
        (new_data or {}).get("entities"),
    )
    return [apply_canonical_rename(series_id, old, new) for old, new in renames]
