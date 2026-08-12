"""analyze_hit_vs_relevance.py — 检索命中（代理指标）vs 真实相关性（LLM judge）对比分析。

用法:
    PYTHONIOENCODING=utf-8 python scripts/dev/analysis/analyze_hit_vs_relevance.py \
        --results scripts/dev/eval_dialogue/data/results/<latest>_results.json \
        --judge   scripts/dev/eval_dialogue/data/judge_results/routed_judge.json
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path


def rate(vals: list) -> tuple:
    nums = [v for v in vals if v is not None and isinstance(v, (int, float))]
    if not nums:
        return (None, 0)
    return (sum(nums) / len(nums), len(nums))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True)
    ap.add_argument("--judge", required=True)
    args = ap.parse_args()

    res = json.loads(Path(args.results).read_text(encoding="utf-8"))
    judge = json.loads(Path(args.judge).read_text(encoding="utf-8"))
    res_rows = {r["case_id"]: r for r in res["results"]}
    judge_rows = {r["case_id"]: r for r in judge["rows"]}
    common = [c for c in judge_rows if c in res_rows]

    print(f"=== 数据版本 ===")
    print(f"results: {res['meta'].get('timestamp','')[:19]} | git {res['meta'].get('git_sha')} | seed v{res['meta'].get('seed_version')} | {res['meta'].get('case_count')} case")
    print(f"judge:   {len(judge_rows)} case | avg self_score={judge.get('avg')}")

    print(f"\n=== 一、代理指标（检索命中）汇总 ===")
    for key, label in [
        ("channel_hit_at_5", "全库通道top5命中"),
        ("channel_hit_at_15", "全库通道top15命中"),
        ("speaker_hit_at_5", "speaker级top5命中"),
        ("speaker_hit_at_15", "speaker级top15命中"),
        ("ndcg_at_5", "NDCG@5"),
        ("semantic_overlap_at_5", "语义重叠@5"),
        ("indoc_hit_at_5", "作品内top5命中"),
        ("indoc_coverage", "作品内覆盖"),
        ("speaker_hit", "口吻角色台词命中"),
    ]:
        vals = [res_rows[c]["metrics"].get(key) for c in common]
        v, n = rate(vals)
        if v is None:
            print(f"  {label:<16} N/A ({n})")
        elif key in ("ndcg_at_5", "semantic_overlap_at_5", "indoc_coverage"):
            print(f"  {label:<16} {v:.3f} (n={n})")
        else:
            print(f"  {label:<16} {v:.1%} ({n}/{len(common)})")

    print(f"\n=== 二、真实相关性（LLM judge self_score）汇总 ===")
    scores = [judge_rows[c]["self_score"] for c in common if judge_rows[c]["self_score"] is not None]
    print(f"  平均 self_score: {sum(scores)/len(scores):.3f} ({len(scores)}/{len(common)})")
    print(f"  完全命中(>=0.9): {sum(1 for s in scores if s>=0.9)} ({sum(1 for s in scores if s>=0.9)/len(scores):.1%})")
    print(f"  低相关(<0.5):    {sum(1 for s in scores if s<0.5)} ({sum(1 for s in scores if s<0.5)/len(scores):.1%})")
    print(f"  完全无关(=0.0):   {sum(1 for s in scores if s==0)} ({sum(1 for s in scores if s==0)/len(scores):.1%})")

    print(f"\n=== 三、按角色（真实相关性）===")
    by_char = defaultdict(list)
    for c in common:
        by_char[judge_rows[c]["character"]].append(judge_rows[c]["self_score"])
    for ch, ss in sorted(by_char.items(), key=lambda x: -(sum(x[1]) / len(x[1]))):
        print(f"  {ch:<8} n={len(ss):<3} avg={sum(ss)/len(ss):.3f} 0分={sum(1 for s in ss if s==0)} 1分={sum(1 for s in ss if s>=0.9)}")

    print(f"\n=== 四、按通道（真实相关性）===")
    by_chan = defaultdict(list)
    for c in common:
        by_chan[judge_rows[c]["channel"]].append(judge_rows[c]["self_score"])
    for ch, ss in by_chan.items():
        print(f"  {ch:<10} n={len(ss):<3} avg={sum(ss)/len(ss):.3f} 0分={sum(1 for s in ss if s==0)} 1分={sum(1 for s in ss if s>=0.9)}")

    print(f"\n=== 五、代理命中 vs 真实相关性（假阳性分析）===")
    # 假阳性: 代理指标命中(speaker级或全库top5) 但真实相关性低
    fp = []
    for c in common:
        m = res_rows[c]["metrics"]
        j = judge_rows[c]
        proxy_hit = (m.get("speaker_hit_at_5") == 1.0) or (m.get("channel_hit_at_5") == 1)
        if proxy_hit and j["self_score"] is not None and j["self_score"] < 0.5:
            fp.append((c, j["self_score"], j["query"][:28], (j["self_reason"] or "")[:40]))
    print(f"  代理命中但真实相关性<0.5: {len(fp)}/{len(common)} ({len(fp)/len(common):.1%})")
    for c, s, q, r in sorted(fp, key=lambda x: x[1])[:25]:
        print(f"    {c:<14} score={s:.2f} | {q} | {r}")

    # 假阴性: 代理未命中但真实相关性高
    fn = []
    for c in common:
        m = res_rows[c]["metrics"]
        j = judge_rows[c]
        proxy_hit = (m.get("speaker_hit_at_5") == 1.0) or (m.get("channel_hit_at_5") == 1)
        if not proxy_hit and j["self_score"] is not None and j["self_score"] >= 0.9:
            fn.append((c, j["self_score"], j["query"][:28]))
    print(f"\n  代理未命中但真实相关性>=0.9: {len(fn)}/{len(common)}")
    for c, s, q in fn[:15]:
        print(f"    {c:<14} score={s:.2f} | {q}")

    print(f"\n=== 六、路由通道分布 ===")
    routed = Counter(res_rows[c]["metrics"].get("routed_channel") for c in common)
    print(f"  {dict(routed)}")
    # 路由 vs 真实相关性
    by_routed = defaultdict(list)
    for c in common:
        rc = res_rows[c]["metrics"].get("routed_channel")
        by_routed[rc].append(judge_rows[c]["self_score"])
    for rc, ss in sorted(by_routed.items(), key=lambda x: -(sum(x[1])/len(x[1]))):
        print(f"  路由到 {rc:<10} n={len(ss):<3} avg={sum(ss)/len(ss):.3f}")

    # 路由与 case 原始通道对比
    mismatch = sum(1 for c in common if res_rows[c]["metrics"].get("routed_channel") != judge_rows[c]["channel"])
    print(f"\n  路由通道≠case标注通道: {mismatch}/{len(common)} ({mismatch/len(common):.1%})")


if __name__ == "__main__":
    main()
