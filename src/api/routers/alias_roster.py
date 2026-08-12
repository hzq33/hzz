"""Alias roster CRUD — read/modify persist_alias_json output."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.domain.novel.series_paths import (
    _DATA,
    alias_json_path,
    safe_series_stem,
    series_stem_aliases,
)

logger = logging.getLogger("agent")

DATA_DIR = _DATA / "rosters"


def read_alias(series_id: str) -> dict[str, Any]:
    """Read alias.json for a series (tries all historical stems)."""
    for stem in series_stem_aliases(series_id):
        path = DATA_DIR / f"{stem}.alias.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    return {"entities": [], "meta": {"error": "not_found"}}


def write_alias(series_id: str, data: dict[str, Any]) -> Path:
    """Write alias.json for a series (canonical stem) and drop legacy stems."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    primary = alias_json_path(series_id)
    data = dict(data or {})
    data["series_id"] = (series_id or "").strip()
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    primary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # Remove duplicate legacy filenames so Monitor / purge stay consistent
    primary_resolved = primary.resolve()
    for stem in series_stem_aliases(series_id):
        other = (DATA_DIR / f"{stem}.alias.json").resolve()
        if other != primary_resolved and other.exists():
            try:
                other.unlink()
            except OSError:
                pass
    logger.info(
        "alias.json written: %s (%d entities)",
        primary,
        len(data.get("entities") or []),
    )
    return primary


def list_series() -> list[str]:
    """List series ids that have alias.json files."""
    if not DATA_DIR.exists():
        return []
    out: list[str] = []
    seen: set[str] = set()
    for path in sorted(DATA_DIR.glob("*.alias.json")):
        # Prefer series_id inside file; fall back to stem
        sid = ""
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            sid = str(raw.get("series_id") or "").strip()
        except (OSError, json.JSONDecodeError, TypeError):
            sid = ""
        if not sid:
            # foo.alias.json → stem is "foo.alias"
            stem = path.name[: -len(".alias.json")] if path.name.endswith(".alias.json") else path.stem
            sid = stem
        key = safe_series_stem(sid)
        if key in seen:
            continue
        seen.add(key)
        out.append(sid)
    return sorted(out)
