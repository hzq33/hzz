"""Cascade cleanup when a series has no remaining volumes."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from src.domain.novel.series_paths import series_stem_aliases

logger = logging.getLogger("agent")

from src.domain.novel.series_paths import data_root

_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _unlink(path: Path, stats: dict[str, Any]) -> None:
    try:
        if path.exists():
            path.unlink()
            stats["files"] = int(stats.get("files") or 0) + 1
            stats.setdefault("paths", []).append(str(path))
    except OSError as exc:
        logger.warning("Failed to delete %s: %s", path, exc)


def purge_series_artifacts(series_id: str) -> dict[str, Any]:
    """Remove roster / inventory / cards / analysis sidecars for a series.

    Called when the last volume of a series is deleted so the Knowledge UI
    and Alias Roster Monitor do not keep showing ghost data.
    """
    sid = (series_id or "").strip()
    stats: dict[str, Any] = {"series_id": sid, "files": 0, "paths": []}
    if not sid:
        return stats

    stems = series_stem_aliases(sid)
    names: list[str] = []
    character_ids: list[str] = []

    try:
        from src.domain.novel.character_roster import load_roster

        roster = load_roster(sid)
        if roster:
            for entry in roster.characters:
                if entry.name:
                    names.append(entry.name)
                if entry.character_id:
                    character_ids.append(entry.character_id)
    except Exception as exc:
        logger.warning("purge: load roster failed for %s: %s", sid, exc)

    try:
        from src.domain.novel.character_inventory import load_inventory_candidates

        inventory = load_inventory_candidates(sid) or {}
        for candidate in inventory.get("candidates") or []:
            name = (candidate.get("name") or "").strip()
            if name and name not in names:
                names.append(name)
    except Exception:
        pass

    # Also harvest names from alias.json before deleting it
    try:
        from src.api.routers.alias_roster import read_alias

        alias = read_alias(sid)
        for ent in alias.get("entities") or []:
            name = str(ent.get("canonical_name") or "").strip()
            if name and name not in names:
                names.append(name)
    except Exception:
        pass

    # Sidecar JSON files — delete every historical stem variant
    for stem in stems:
        for relative in (
            f"data/rosters/{stem}.json",
            f"data/rosters/{stem}.alias.json",
            f"data/inventories/{stem}.json",
            f"data/story_analyses/{stem}.json",
            f"data/catalogs/{stem}.json",
            f"data/graphs/{stem}.json",
        ):
            _unlink(_PROJECT_ROOT / relative, stats)
        graphs_dir = data_root() / "graphs"
        if graphs_dir.exists():
            for path in graphs_dir.glob(f"{stem}__*.json"):
                _unlink(path, stats)

    # Content-based sweep (files that embed series_id)
    for sub in ("rosters", "inventories", "catalogs", "story_analyses"):
        folder = data_root() / sub
        if not folder.exists():
            continue
        for path in folder.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, TypeError):
                continue
            if str(data.get("series_id") or "").strip() == sid:
                _unlink(path, stats)

    # Character cards (series-scoped + legacy bare name files belonging to series)
    try:
        from src.domain.character_card import CharacterCard
        from src.domain.novel.character_roster import character_id_for

        cache_dir = CharacterCard._CACHE_DIR
        cache_dir.mkdir(parents=True, exist_ok=True)
        for name in names:
            cid = character_id_for(sid, name)
            character_ids.append(cid)
            _unlink(CharacterCard.cache_path_for(sid, name, character_id=cid), stats)
            _unlink(cache_dir / f"{name}.json", stats)

        for cid in sorted(set(character_ids)):
            _unlink(cache_dir / f"{cid}.json", stats)

        for path in cache_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            card_series = str(data.get("series_id") or "").strip()
            source_work = str(data.get("source_work") or "").strip()
            if card_series == sid or source_work == sid:
                _unlink(path, stats)
    except Exception as exc:
        logger.warning("purge: character cards failed for %s: %s", sid, exc)

    logger.info(
        "Purged series artifacts series=%s files=%d",
        sid,
        stats.get("files") or 0,
    )
    return stats
