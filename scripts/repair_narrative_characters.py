"""One-time repair: backfill characters_json for existing narrative blocks.

Existing narrative blocks were ingested without a character name list, so
their characters_json is empty (the all_person data debt: 4898/4898 blocks).
This script re-tags them from series-level name sources (alias map ∪ inventory
candidates) using the same tiered matcher as chunk-time tagging, then updates
LanceDB in place (characters_json column only — vectors untouched).

Usage:
    python scripts/repair_narrative_characters.py            # dry-run (report only)
    python scripts/repair_narrative_characters.py --apply    # write to LanceDB

Requires: run from repo root with the project venv.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import lancedb

from src.domain.novel.chunker import match_known_persons

_LANCE_PATH = Path("data/novel_lance")
from src.domain.novel.series_paths import (
    alias_json_path,
    inventory_json_path,
    roster_json_path,
)

_ALIAS_DIR = Path("data/rosters")  # 仅 L1 roster 兜底目录


def _series_names(series_id: str, cache: dict) -> list[str]:
    if series_id in cache:
        return cache[series_id]
    names: list[str] = []
    # 1) series alias map: {series}.alias.json
    alias_file = alias_json_path(series_id)
    if alias_file.exists():
        try:
            amap = json.loads(alias_file.read_text(encoding="utf-8"))
            names += [
                str(e.get("canonical_name", "")).strip()
                for e in amap.get("entities") or []
                if e.get("canonical_name")
            ]
        except Exception as e:
            print(f"  ! alias load failed {alias_file.name}: {e}", file=sys.stderr)
    # 2) inventory candidates: 精确按系列定位（series_paths.inventory_json_path）
    inv_file = inventory_json_path(series_id)
    if inv_file and inv_file.exists():
        try:
            payload = json.loads(inv_file.read_text(encoding="utf-8"))
            names += [
                str(c.get("name", "")).strip()
                for c in payload.get("candidates") or []
                if c.get("name")
            ]
        except Exception:
            pass
    # 3) L1 roster: data/rosters/{stem}.json across all stem variants
    from src.domain.novel.series_paths import series_stem_aliases

    for stem in series_stem_aliases(series_id):
        roster_file = roster_json_path(stem)
        if not roster_file.exists():
            continue
        try:
            payload = json.loads(roster_file.read_text(encoding="utf-8"))
            names += [
                str(c.get("name", "")).strip()
                for c in payload.get("characters") or []
                if c.get("name")
            ]
        except Exception:
            continue
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    cache[series_id] = out
    return out


def main() -> int:
    apply = "--apply" in sys.argv
    if not _LANCE_PATH.exists():
        print(f"LanceDB path not found: {_LANCE_PATH}")
        return 1
    db = lancedb.connect(str(_LANCE_PATH))
    tbl = db.open_table("novel_blocks")
    df = tbl.to_pandas()
    narr = df[df["block_type"] == "narrative"]
    print(f"narrative blocks: {len(narr)} (total rows {len(df)})")

    cache: dict[str, list[str]] = {}
    updated = 0
    already = 0
    no_names = 0
    for _, row in narr.iterrows():
        doc_id = str(row.get("doc_id") or "")
        series_id = doc_id.split("__")[0] if "__" in doc_id else doc_id
        if not series_id:
            continue
        names = _series_names(series_id, cache)
        if not names:
            no_names += 1
            continue
        text = str(row.get("narrative_text") or "")
        found = match_known_persons(text, names)
        if not found:
            continue
        existing_raw = row.get("characters_json") or "[]"
        try:
            existing = json.loads(existing_raw) if existing_raw else []
        except (TypeError, ValueError):
            existing = []
        merged = list(existing)
        for n in found:
            if n not in merged:
                merged.append(n)
        if not merged:
            continue
        if not apply:
            updated += 1
            continue
        gid = str(row.get("global_id") or "")
        if not gid:
            continue
        try:
            tbl.update(
                where=f"global_id = '{gid.replace(chr(39), chr(39) * 2)}'",
                values={"characters_json": json.dumps(merged, ensure_ascii=False)},
            )
            updated += 1
        except Exception as e:
            print(f"  ! update failed {gid}: {e}", file=sys.stderr)

    print(
        f"{'APPLIED' if apply else 'DRY-RUN'} result: "
        f"would-update/updated={updated}, already-tagged={already}, "
        f"series-without-names={no_names}"
    )
    if not apply:
        print("Re-run with --apply to write changes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
