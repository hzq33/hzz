"""别名归并结果 → 校验 → alias.json 重建。

设计: docs/ALIAS_UNIFICATION_DESIGN.md B 层
输入: alias_merge_probe_{series}.json（LLM 归并 groups: [{canonical, variants, reason}]）
流程: canonical 原文校验 → 一对一等价校验 → 写 data/rosters/{series}.alias.json

用法:
    PYTHONPATH="./venv/Lib/site-packages" ./venv/Scripts/python.exe \
        scripts/dev/analysis/rebuild_alias.py "败犬女主太多了" [--check] [--no-verify]

校验规则（validate_merge_groups）:
    E1 变体唯一：任一变体只能出现在一个 group（防"温水"同时映射两人）
    E2 canonical 唯一：canonical 不重复
    E3 canonical 非空且 ≥2 字
    W1 canonical 未在原文出现（仅提示，LLM 可能截断全名）
    W2 canonical 短于组内某变体（可能截断，如 坦派斯 vs 坦派斯特）
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.domain.novel.alias_merge import validate_merge_groups  # noqa: E402


def verify_in_text(names: list[str], series_id: str) -> dict[str, bool]:
    """原文出现校验：名字是否在系列正文中出现（LanceDB narrative 聚合）。"""
    import lancedb

    db = lancedb.connect(str(ROOT / "data" / "novel_lance"))
    df = db.open_table("novel_blocks").to_pandas()
    # doc_id 用空格分隔（文件名为下划线）——两种都试
    prefixes = {series_id, series_id.replace("_", " ")}
    mask = df["doc_id"].str.startswith(tuple(prefixes), na=False) & (
        df["block_type"] == "narrative"
    )
    text = "\n".join(df.loc[mask, "narrative_text"].dropna().astype(str).tolist())
    return {n: bool(re.search(re.escape(n), text)) for n in names}


def main(series_id: str, check_only: bool, verify: bool) -> int:
    probe_path = (
        Path(__file__).resolve().parent.parent
        / "verify" / "tmp" / f"alias_merge_probe_{series_id}.json"
    )
    if not probe_path.exists():
        print(f"[rebuild] 探针结果不存在: {probe_path}")
        return 2
    data = json.loads(probe_path.read_text(encoding="utf-8"))
    groups = data.get("groups", [])
    print(f"[rebuild] {series_id}: {len(groups)} 组")

    errors, warnings = validate_merge_groups(groups)
    for w in warnings:
        print(f"  ⚠ {w}")
    if errors:
        print("[rebuild] ❌ 校验失败，不得落盘:")
        for e in errors:
            print(f"  ✗ {e}")
        return 1

    if verify:
        all_names = [g["canonical"] for g in groups]
        for g in groups:
            all_names += g.get("variants") or []
        presence = verify_in_text(all_names, series_id)
        missing = [n for n in all_names if not presence.get(n, True)]
        if missing:
            print(f"[rebuild] ⚠ canonical/变体未在原文出现（{len(missing)}）: {missing[:10]}")

    if check_only:
        print("[rebuild] ✅ 校验通过（--check，未写文件）")
        return 0

    # 重建 alias.json（对齐现有格式）
    entities = []
    for g in groups:
        canon = str(g["canonical"]).strip()
        variants = [str(v).strip() for v in (g.get("variants") or []) if str(v).strip()]
        entities.append(
            {
                "character_id": f"{series_id}__{canon}",
                "canonical_name": canon,
                "aliases": variants,
                "confidence": 0.9,
                "source": {"llm_merge": True, "reason": str(g.get("reason") or "")[:200]},
            }
        )
    out_path = ROOT / "data" / "rosters" / f"{series_id}.alias.json"
    out_path.write_text(
        json.dumps(
            {"series_id": series_id, "entities": entities}, ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )
    print(f"[rebuild] ✅ 已重建 {out_path}（{len(entities)} 组）")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="归并结果 → alias.json 重建")
    ap.add_argument("series_id", help="系列名")
    ap.add_argument("--check", action="store_true", help="只校验不写文件")
    ap.add_argument("--no-verify", action="store_true", help="跳过原文出现校验")
    args = ap.parse_args()
    sys.exit(main(args.series_id, args.check, not args.no_verify))
