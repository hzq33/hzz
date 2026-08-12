"""从 inventory candidates 补回 roster + alias.json 中缺失的角色。

手误删除角色（delete_character API）只会清理 roster + alias.json + 角色卡，
不会动 inventory candidates（NER 产物）。本脚本读 candidates，把 roster 缺失
的角色补回去，然后重建 alias.json。

用法:
    python scripts/dev/repair_roster_from_inventory.py --series 败犬女主太多了
    python scripts/dev/repair_roster_from_inventory.py --series 败犬女主太多了 --dry-run
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def load_inventory_candidates(series_id: str) -> dict | None:
    path = _DATA_DIR / "inventories" / f"{series_id}.json"
    if not path.exists():
        print(f"  [ERROR] inventory not found: {path}")
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_roster(series_id: str) -> dict | None:
    path = _DATA_DIR / "rosters" / f"{series_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_roster(roster: dict) -> None:
    path = _DATA_DIR / "rosters" / f"{roster['series_id']}.json"
    path.write_text(json.dumps(roster, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  roster saved: {path}")


def load_alias_map(series_id: str) -> dict | None:
    path = _DATA_DIR / "rosters" / f"{series_id}.alias.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_alias_map(amap: dict) -> None:
    path = _DATA_DIR / "rosters" / f"{amap['series_id']}.alias.json"
    path.write_text(json.dumps(amap, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  alias.json saved: {path}")


def character_id_for(series_id: str, name: str) -> str:
    import re
    slug = re.sub(r"[^\w\u4e00-\u9fff]+", "_", name.strip()).strip("_") or "unknown"
    return f"{series_id}__{slug}"


def repair(series_id: str, *, dry_run: bool = False) -> None:
    print(f"=== repair roster from inventory ===")
    print(f"  series_id: {series_id}")
    print(f"  dry_run: {dry_run}")

    inv = load_inventory_candidates(series_id)
    if not inv:
        return
    inv_cands = inv.get("candidates", [])
    inv_names = {c["name"] for c in inv_cands}
    print(f"  inventory candidates: {len(inv_cands)}")

    roster = load_roster(series_id) or {
        "series_id": series_id,
        "doc_ids": inv.get("doc_ids", []),
        "updated_at": "",
        "characters": [],
    }
    ros_names = {c["name"] for c in roster.get("characters", [])}
    print(f"  roster characters: {len(ros_names)}")

    missing = inv_names - ros_names
    print(f"  missing from roster: {len(missing)}")
    if missing:
        for name in sorted(missing):
            c = next(c for c in inv_cands if c["name"] == name)
            print(f"    + {name} (aliases={c.get('aliases', [])}, mentions={c.get('mention_count', 0)})")

    if not missing:
        print("  nothing to repair in roster")
    elif not dry_run:
        # 补回缺失角色
        existing = {c["name"]: c for c in roster.get("characters", [])}
        for name in sorted(missing):
            c = next(c for c in inv_cands if c["name"] == name)
            existing[name] = {
                "name": name,
                "aliases_observed": c.get("aliases", []),
                "dialogue_count": 0,
                "mention_count": c.get("mention_count", 0),
                "chapters": [],
                "co_occurrence": {},
                "status": "candidate",
                "character_id": character_id_for(series_id, name),
                "has_card": False,
            }
        merged = sorted(
            existing.values(),
            key=lambda e: (-e.get("dialogue_count", 0), -e.get("mention_count", 0), e["name"]),
        )
        roster["characters"] = merged
        from datetime import datetime, timezone
        roster["updated_at"] = datetime.now(timezone.utc).isoformat()
        save_roster(roster)

    # 重建 alias.json：从 inventory candidates 补回缺失 entity
    # 注意：inventory candidates 可能有重复 canonical（不同卷 NER 给出不同 canonical_name，
    # 但 alias.json 已经把它们合并了）。只补"真缺失"的——即该角色的 name 和 aliases
    # 都不在现有 alias.json 的任何 entity 里。
    amap = load_alias_map(series_id) or {
        "series_id": series_id,
        "entities": [],
        "updated_at": "",
    }
    amap_names = {e["canonical_name"] for e in amap.get("entities", [])}
    print(f"  alias.json entities: {len(amap_names)}")

    # 构建所有已知名字集合（canonical + aliases）
    all_known_names: set[str] = set()
    for e in amap.get("entities", []):
        all_known_names.add(e["canonical_name"])
        all_known_names.update(e.get("aliases", []))

    alias_missing: list[str] = []
    alias_duplicates: list[str] = []
    for name in inv_names - amap_names:
        c = next(c for c in inv_cands if c["name"] == name)
        cand_names = {name, *c.get("aliases", [])}
        # 如果 name 本身就是某个现有 entity 的 alias，说明是重复 canonical，跳过
        if name in all_known_names:
            alias_duplicates.append(name)
            continue
        # 如果所有 aliases 都在现有 entity 里，也是重复
        if cand_names - all_known_names:
            alias_missing.append(name)
        else:
            alias_duplicates.append(name)

    print(f"  missing from alias.json: {len(alias_missing)} (真缺失)")
    if alias_missing:
        for name in sorted(alias_missing):
            c = next(c for c in inv_cands if c["name"] == name)
            print(f"    + {name} (aliases={c.get('aliases', [])})")
    if alias_duplicates:
        print(f"  skipped (duplicate canonical, already merged): {len(alias_duplicates)}")
        for name in sorted(alias_duplicates):
            print(f"    ~ {name}")

    if not alias_missing:
        print("  nothing to repair in alias.json")
    elif not dry_run:
        existing_entities = {e["canonical_name"]: e for e in amap.get("entities", [])}
        for name in sorted(alias_missing):
            c = next(c for c in inv_cands if c["name"] == name)
            existing_entities[name] = {
                "character_id": "",
                "canonical_name": name,
                "aliases": c.get("aliases", []),
                "titles": [],
                "confidence": 0.8,
                "source": {"repaired_from": "inventory_candidates"},
            }
        amap["entities"] = sorted(existing_entities.values(), key=lambda e: e["canonical_name"])
        from datetime import datetime, timezone
        amap["updated_at"] = datetime.now(timezone.utc).isoformat()
        save_alias_map(amap)

    if dry_run:
        print("\n  [DRY RUN] no files written. Re-run without --dry-run to apply.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Repair roster + alias.json from inventory candidates")
    parser.add_argument("--series", required=True, help="series_id (e.g. 败犬女主太多了)")
    parser.add_argument("--dry-run", action="store_true", help="only report, don't write")
    args = parser.parse_args()
    repair(args.series, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
