"""Compare legacy median vs hybrid seed policy on REAL book inventories.

Follow-up item 1 from MODULE_SPLIT_AND_SEED_HYBRID_WORKLOG.md §7:
  - seed_size / prompt_cand distribution
  - protagonist retention
  - species-name blocking (史莱姆 etc.)

Uses real persisted inventory JSON + real volume-07 chapter text. Offline, no LLM.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "buffer"):
    sys.stdout = __import__("io").TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

from src.domain.novel.character_inventory.candidates import (
    as_inventory_character,
    build_llm_seed,
    percentile_mention_threshold,
)
from src.domain.novel.dialogue_quota import DEFAULT_IMPORTANCE_BLACKLIST
from src.domain.novel.series_paths import inventory_json_path

ROOT = Path(__file__).resolve().parents[3]

INVENTORIES = [
    inventory_json_path("关于我转生变成史莱姆这档事"),
    inventory_json_path("关于我转生变莱姆这档事_维鲁多拉的史莱姆成史观察日记"),
]
VOL07 = ROOT / "data" / "analysis_texts" / "vol07_cleaned.md"

HYBRID_CFG = {
    "seed": {
        "mode": "hybrid",
        "min_mentions": 2,
        "percentile": 70,
        "top_k": 30,
        "small_n_fallback": 8,
        "small_n_top": 8,
        "blacklist_from_quota": True,
    }
}


def legacy_median_seed(chars) -> list:
    """Faithful simulation of the OLD policy: median threshold, no blacklist,
    no top-k (worklog: 龙套密集偏松 / 物种名进 prompt / 无硬 Top-K)."""
    thr = percentile_mention_threshold(chars, percentile=50, floor=1)
    picked = sorted(
        (c for c in chars if c.mention_count >= thr),
        key=lambda x: (-x.mention_count, x.canonical_name),
    )
    return picked, thr


def load_chars(path: Path) -> list:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [as_inventory_character(c) for c in data.get("candidates") or []]


def summarize(names: list[str], chars) -> dict:
    by = {c.canonical_name: c for c in chars}
    species_hit = [n for n in names if n in set(DEFAULT_IMPORTANCE_BLACKLIST)]
    protagonists = [
        n
        for n in names
        if by.get(n) and by[n].importance == "main"
    ]
    return {
        "seed_size": len(names),
        "species_in_seed": species_hit,
        "protagonists_kept": protagonists,
        "top5": names[:5],
    }


def main() -> None:
    report: list[str] = []
    report.append("# Seed Policy Comparison: legacy median vs hybrid (real books)\n")

    for inv_path in INVENTORIES:
        chars = [c for c in load_chars(Path(inv_path)) if c is not None]
        legacy, legacy_thr = legacy_median_seed(chars)
        hybrid = build_llm_seed(chars, config=HYBRID_CFG)

        legacy_names = [c.canonical_name for c in legacy]
        hybrid_names = [c.canonical_name for c in hybrid.characters]

        report.append(f"\n## {Path(inv_path).stem}\n")
        report.append(f"- candidates_total={len(chars)}")
        report.append(
            f"- legacy median: thr={legacy_thr} seed_size={len(legacy_names)}"
        )
        report.append(
            f"- hybrid:        thr={hybrid.threshold} mode={hybrid.mode} "
            f"top_k={hybrid.top_k} seed_size={len(hybrid_names)} "
            f"blacklisted={hybrid.blacklisted}"
        )
        for label, names in (("legacy", legacy_names), ("hybrid", hybrid_names)):
            s = summarize(names, chars)
            report.append(
                f"  - {label}: species_in_seed={s['species_in_seed'] or '∅'} "
                f"protagonists_kept={len(s['protagonists_kept'])} "
                f"top5={s['top5']}"
            )
        report.append(
            f"  - dropped by hybrid vs legacy: "
            f"{sorted(set(legacy_names) - set(hybrid_names))[:8]}"
        )
        report.append(
            f"  - added by hybrid vs legacy: "
            f"{sorted(set(hybrid_names) - set(legacy_names))[:8]}"
        )

    # ── prompt_cand distribution on real vol07 text ──────────────
    from src.application.novel.dialogue_pipeline import assemble_prompt_candidates

    text = Path(VOL07).read_text(encoding="utf-8") if Path(VOL07).exists() else ""
    chapters = re.split(r"\n#+\s+", text)
    chapters = [c.strip() for c in chapters if len(c.strip()) > 200]

    report.append("\n## prompt_cand distribution (vol07 real chapters)\n")
    report.append(
        f"- chapters_used={len(chapters)} max_n=10 prefer_local=True"
    )

    inv = [c for c in load_chars(Path(INVENTORIES[0])) if c is not None]
    legacy_names = [c.canonical_name for c in legacy_median_seed(inv)[0]]
    hybrid_names = [c.canonical_name for c in build_llm_seed(inv, config=HYBRID_CFG).characters]

    stats = {"legacy": {"n": 0, "local_hit": 0, "seed_hit": 0, "species": 0},
             "hybrid": {"n": 0, "local_hit": 0, "seed_hit": 0, "species": 0}}
    for ch in chapters[:10]:
        for label, seed in (("legacy", legacy_names), ("hybrid", hybrid_names)):
            cands = assemble_prompt_candidates(
                volume_seed=seed, chapter_text=ch, spans=None, max_n=10, prefer_local=True
            )
            st = stats[label]
            st["n"] += len(cands)
            st["local_hit"] += sum(1 for c in cands if c in ch)
            st["seed_hit"] += sum(1 for c in cands if c in seed)
            st["species"] += sum(1 for c in cands if c in set(DEFAULT_IMPORTANCE_BLACKLIST))
    for label, st in stats.items():
        report.append(
            f"- {label}: avg_cands={st['n']/max(1,len(chapters[:10])):.1f} "
            f"local_hit={st['local_hit']} seed_hit={st['seed_hit']} "
            f"species_in_prompt={st['species']}"
        )

    out = "\n".join(report)
    print(out)
    dst = ROOT / "docs/analysis/SEED_MEDIAN_VS_HYBRID_2026-08-05.md"
    dst.write_text(out + "\n", encoding="utf-8")
    print(f"\nWROTE {dst}")


if __name__ == "__main__":
    main()
