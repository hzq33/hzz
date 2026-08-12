"""100 次扮演评估 — 指标分析。

输入：data/eval/impersonation_100_results.jsonl + /metrics 回收指标
输出：分析报告（回答质量 / 引用分布 / 失败诊断 / 问题清单）
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict

import requests

TOKEN = os.environ.get("AGENT_API_TOKEN", "")
BASE = "http://127.0.0.1:8080"
H = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}

RESULTS = "data/eval/impersonation_100_results.jsonl"

# 每个角色应该知道的事（知识边界检查：检索到但角色不该知道）
# 来自小说的设定：老首领已死（角色若回答"我还活着"就是错的）
# 马库斯已死（亚瑟父亲）


def load_results(path: str = RESULTS) -> list[dict]:
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def analyze() -> None:
    rows = load_results()
    if not rows:
        print("无结果文件")
        return
    total = len(rows)
    errs = [r for r in rows if r.get("error")]
    ok = [r for r in rows if not r.get("error")]

    print("=" * 60)
    print("100 次扮演评估 — 指标分析")
    print("=" * 60)
    print(f"总问题: {total}  成功: {len(ok)}  失败: {len(errs)}  成功率: {len(ok)/total*100:.1f}%")

    # 1. 引用分析
    with_cit = [r for r in ok if r.get("n_citations", 0) > 0]
    print(f"\n--- 引用 ---")
    print(f"带引用: {len(with_cit)}/{len(ok)} ({len(with_cit)/max(len(ok),1)*100:.1f}%)")
    cit_dist = Counter(r["n_citations"] for r in ok)
    print("引用数分布:", dict(sorted(cit_dist.items())))

    # 2. 按方向分析
    print(f"\n--- 按方向 ---")
    dir_stats = {}
    for d in ("daily", "relation", "temporal", "event"):
        sub = [r for r in ok if r["direction"] == d]
        if not sub:
            continue
        c = [r for r in sub if r.get("n_citations", 0) > 0]
        avg_lat = sum(r.get("latency_s", 0) for r in sub) / len(sub)
        dir_stats[d] = {"n": len(sub), "cit_rate": len(c) / len(sub)}
        print(f"  {d}: {len(sub)} 问, 带引用 {len(c)} ({len(c)/len(sub)*100:.0f}%), 平均耗时 {avg_lat:.0f}s")

    # 3. 按角色分析
    print(f"\n--- 按角色 ---")
    for ch, sub in sorted(group_by(ok, "character").items()):
        c = [r for r in sub if r.get("n_citations", 0) > 0]
        print(f"  {ch}: {len(sub)} 问, 带引用 {len(c)} ({len(c)/len(sub)*100:.0f}%)")

    # 4. 知识边界检查（老首领已死 — 角色若答"还活着"是错的）
    print(f"\n--- 知识边界检查 ---")
    dead_knowledge = {
        "老首领": ["死", "亡", "不在了", "去世", "走了"],
    }
    for ch, sub in group_by(ok, "character").items():
        if ch == "老首领":
            # 老首领问自己怎么死的——如果回答"我还活着"就错了
            for r in sub:
                reply = r.get("reply", "")
                if "我还活着" in reply or "我还没死" in reply:
                    print(f"  ⚠ {ch} 问「{r['question'][:20]}」答『我还活着』— 知识边界错误?")
                else:
                    print(f"  ✓ {ch} 问「{r['question'][:20]}」— {reply[:40]}")

    # 5. 回答质量粗判（关键词命中）
    print(f"\n--- 回答质量抽查（关键实体命中） ---")
    checks = {
        ("亚瑟·卡恩", "relation", "莉娜"): ["莉娜"],
        ("亚瑟·卡恩", "relation", "维克托"): ["维克托", "父亲"],
        ("亚瑟·卡恩", "event", "黑鸦堡"): ["黑鸦堡", "老首领"],
        ("莉娜·沃伦", "relation", "亚瑟"): ["亚瑟"],
        ("维克托·黑森", "relation", "马库斯"): ["马库斯"],
        ("艾琳·塔利斯", "event", "母亲"): ["母亲", "母"],
        ("雷恩·索恩", "event", "黑旗军"): ["黑旗", "甲片"],
        ("玛拉·霍恩", "event", "永冻之心"): ["永冻"],
    }
    for (ch, d, kw), kws in checks.items():
        hits = [r for r in ok if r["character"] == ch and r["direction"] == d]
        for r in hits:
            reply = r.get("reply", "")
            matched = [k for k in kws if k in reply]
            mark = "✓" if matched else "✗"
            print(f"  {mark} {ch} 问「{r['question'][:24]}」命中 {matched or '无'}")

    # 6. /metrics 回收指标
    print(f"\n--- /metrics 回收指标 ---")
    try:
        m = requests.get(f"{BASE}/metrics", timeout=10).text
        for pat in ("retrieval_relevance", "tool_value", "answer_coverage"):
            lines = [l for l in m.splitlines() if pat in l and l.startswith("agent_") and "_total" in l]
            for l in lines:
                print("  ", l)
    except Exception as e:
        print(f"  metrics 获取失败: {e}")

    # 7. 失败诊断
    if errs:
        print(f"\n--- 失败诊断 ---")
        for e in errs[:8]:
            print(f"  [{e['idx']}] {e['character']}「{e['question'][:20]}」→ {e['error'][:90]}")


def group_by(rows: list[dict], key: str) -> dict[str, list[dict]]:
    g = defaultdict(list)
    for r in rows:
        g[r.get(key, "?")].append(r)
    return dict(g)


if __name__ == "__main__":
    analyze()
