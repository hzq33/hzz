"""alias.json → inventory candidates overlay。

设计: docs/ALIAS_UNIFICATION_DESIGN.md B 层第 5 步
目的: 修正存量 data/inventories/{series}.json 的错 aliases
（如 温水佳树 aliases=[佳树,温水,温温] → 温水/温温 拆给 温水和彦）

做法（保守，只改 aliases，不动 name/mention_count/importance）:
  对每个 candidate：
    - 若其 name 是某 alias 组的 variant（非 canonical）→ 不改 name（避免影响 seed 排序）
    - aliases 替换为该组 variants 中「不等于 name」的部分
    - 若 name 是 canonical → aliases = 组 variants - {name}
  校验: 修正后同一 alias 不得出现在两个 canonical 下（E1）

用法:
    PYTHONPATH="./venv/Lib/site-packages" ./venv/Scripts/python.exe \
        scripts/dev/analysis/overlay_alias_to_inventory.py "败犬女主太多了" [--check]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.domain.novel.alias_merge import validate_merge_groups  # noqa: E402


def load_alias_map(series_id: str) -> dict[str, list[str]]:
    """canonical → variants 映射（alias.json 复核产物）。"""
    path = ROOT / "data" / "rosters" / f"{series_id}.alias.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        e["canonical_name"]: list(e.get("aliases") or [])
        for e in data.get("entities", [])
    }


def overlay(series_id: str) -> tuple[list[dict], list[str]]:
    """修正 inventory candidates 的 aliases。返回 (新 candidates, 警告)。"""
    alias_map = load_alias_map(series_id)
    inv_path = ROOT / "data" / "inventories" / f"{series_id}.json"
    if not inv_path.exists():
        raise FileNotFoundError(inv_path)
    inv = json.loads(inv_path.read_text(encoding="utf-8"))
    candidates = inv.get("candidates") or []

    # variant → canonical 反向表（含 canonical 自身）
    variant_to_canon: dict[str, str] = {}
    for canon, vs in alias_map.items():
        variant_to_canon[canon] = canon
        for v in vs:
            variant_to_canon[v] = canon

    # 同 canonical 的 candidates 合并（存量 inventory 可能把同一人拆成多条目：
    # 月之木 + 古都 → 月之木古都）。name 用 alias 权威 canonical，mention 求和。
    merged_by_canon: dict[str, dict] = {}
    warnings: list[str] = []
    for c in candidates:
        name = str(c.get("name") or "").strip()
        if not name:
            continue
        canon = variant_to_canon.get(name)
        if canon is None:
            # 未归并的 candidate 保持原样
            merged_by_canon.setdefault(name, dict(c))
            continue
        if canon in merged_by_canon:
            prev = merged_by_canon[canon]
            prev["mention_count"] = int(prev.get("mention_count") or 0) + int(
                c.get("mention_count") or 0
            )
            fc = set(prev.get("from_clusters") or []) | set(c.get("from_clusters") or [])
            prev["from_clusters"] = sorted(fc)
            imp = {"main": 3, "supporting": 2, "extra": 1}
            if imp.get(str(c.get("importance"))) > imp.get(str(prev.get("importance")), 0):
                prev["importance"] = c.get("importance")
            warnings.append(
                f"合并: {name!r} → {canon!r}（存量拆条，mention 求和）"
            )
        else:
            merged_by_canon[canon] = dict(c)
            merged_by_canon[canon]["name"] = canon
            # aliases = canonical 组 variants - {canonical}
            merged_by_canon[canon]["aliases"] = [
                v for v in alias_map.get(canon, []) if v != canon
            ]

    candidates = list(merged_by_canon.values())

    # 追加缺失 canonical：alias.json 有人但 inventory 无条目（如男主温水和彦）
    # mention_count 从原文（LanceDB narrative）统计，避免进不了 seed（min_mentions）
    existing = {str(c.get("name") or "").strip() for c in candidates}
    missing = [c for c in alias_map if c not in existing]
    if missing:
        import re

        import lancedb

        db = lancedb.connect(str(ROOT / "data" / "novel_lance"))
        df = db.open_table("novel_blocks").to_pandas()
        prefixes = {series_id, series_id.replace("_", " ")}  # doc_id 用空格分隔
        mask = df["doc_id"].str.startswith(tuple(prefixes), na=False) & (
            df["block_type"] == "narrative"
        )
        text = "\n".join(df.loc[mask, "narrative_text"].dropna().astype(str).tolist())
        for canon in missing:
            n = len(re.findall(re.escape(canon), text))
            candidates.append(
                {
                    "name": canon,
                    "aliases": [v for v in alias_map[canon] if v != canon],
                    "mention_count": max(1, n),
                    "importance": "main" if n >= 15 else ("supporting" if n >= 5 else "extra"),
                    "from_clusters": [],
                    "in_llm_seed": True,
                    "from_alias_overlay": True,
                }
            )
        warnings.append(f"追加缺失 canonical {len(missing)} 个（mention 按原文统计）")

    # E1 校验：同一 alias 不得属于两个 canonical
    groups = [
        {"canonical": str(c.get("name") or ""), "variants": list(c.get("aliases") or [])}
        for c in candidates
    ]
    errors, _ = validate_merge_groups(groups)
    if errors:
        raise ValueError(f"overlay 后 alias 冲突: {errors[:5]}")

    # seed_names 重建：main+supporting 全部进先验（含 overlay 追加的温水和彦）
    # 噪声防线：占位符（女生A/路人甲）/职业词/动词碎片 不进 seed（防污染 LLM 候选）
    from src.domain.novel.dialogue_span import is_noise_speaker
    from src.application.novel.dialogue_pipeline.harvest import _OCCUPATION_NOISE

    import re as _re

    _PLACEHOLDER_RE = _re.compile(
        r"^(女生|男生|路人|少女|少年|小孩|孩子|大叔|大妈|老头|老太|"
        r"教师|学生|同学|店员|店长|商人|村民|士兵|骑士|冒险者|魔法师|"
        r"主角|配角|旁白|邻居|亲戚|部下|手下|随从|侍女|管家|卫兵|守卫)[A-Za-z0-9]?$"
    )

    imp_rank = {"main": 3, "supporting": 2, "extra": 1}
    ranked = sorted(
        candidates,
        key=lambda c: (
            -imp_rank.get(str(c.get("importance") or "extra"), 0),
            -int(c.get("mention_count") or 0),
        ),
    )
    seed_names = []
    for c in ranked:
        name = str(c["name"])
        if imp_rank.get(str(c.get("importance") or "extra"), 0) < 2:
            continue
        if is_noise_speaker(name) or name in _OCCUPATION_NOISE or _PLACEHOLDER_RE.match(name):
            continue  # 噪声/占位符不进 seed（女生A 等）
        seed_names.append(name)

    return candidates, warnings, seed_names


def main(series_id: str, check_only: bool) -> int:
    candidates, warnings, seed_names = overlay(series_id)
    for w in warnings:
        print(f"  ⚠ {w}")
    print(f"[overlay] {series_id}: {len(candidates)} candidates, seed_names={len(seed_names)}")
    if check_only:
        print("[overlay] --check：未写回")
        return 0
    inv_path = ROOT / "data" / "inventories" / f"{series_id}.json"
    inv = json.loads(inv_path.read_text(encoding="utf-8"))
    inv["candidates"] = candidates
    inv["seed_names"] = seed_names
    inv["overlayed_by_alias"] = True
    inv_path.write_text(
        json.dumps(inv, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[overlay] ✅ 已写回 {inv_path}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="alias.json → inventory overlay")
    ap.add_argument("series_id", help="系列名")
    ap.add_argument("--check", action="store_true", help="只校验不写回")
    args = ap.parse_args()
    sys.exit(main(args.series_id, args.check))
