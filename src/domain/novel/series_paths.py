"""Canonical on-disk path helpers for series sidecars.

Writers historically mixed ``replace(" ", "_")`` and regex stems; purge must
delete the same files ingest wrote.
"""

from __future__ import annotations

import re
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_DATA = _PROJECT_ROOT / "data"


def safe_series_stem(series_id: str) -> str:
    """Primary stem used by catalog / roster / alias_map / purge."""
    return re.sub(r"[^\w\u4e00-\u9fff\-]+", "_", (series_id or "").strip()) or "unknown"


def series_stem_aliases(series_id: str) -> list[str]:
    """All stems a series might have been written under (for cleanup)."""
    sid = (series_id or "").strip()
    if not sid:
        return []
    stems = [safe_series_stem(sid), sid.replace(" ", "_"), sid]
    # de-dupe preserving order
    out: list[str] = []
    seen: set[str] = set()
    for s in stems:
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def roster_json_path(series_id: str) -> Path:
    return _DATA / "rosters" / f"{safe_series_stem(series_id)}.json"


def alias_json_path(series_id: str) -> Path:
    return _DATA / "rosters" / f"{safe_series_stem(series_id)}.alias.json"


def inventory_json_path(series_id: str) -> Path:
    return _DATA / "inventories" / f"{safe_series_stem(series_id)}.json"


def catalog_json_path(series_id: str) -> Path:
    return _DATA / "catalogs" / f"{safe_series_stem(series_id)}.json"
