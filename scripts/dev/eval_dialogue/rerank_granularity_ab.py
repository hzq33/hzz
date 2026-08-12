"""rerank_granularity_ab.py — 验证 rerank 输入粒度对排序质量的影响。

对 eval_seed 的 narrative 通道 case（有 gold 的）：
  1. store.search 取 top-15 child 候选（现状：150 字 child 向量命中）
  2. 对每个候选构造两种 rerank 输入：
       A. child 原文（现状，~150 字）
       B. 该 child 的 parent ±邻域展开文本（~800-2300 字，生产链路 LLM 实际看到）
  3. 分别用 BGEReranker 打分取 top-5，用 gold_variants 子串判定命中率

输出 A vs B 的 hit@5 / ndcg@5 对比。只读检索，不改库。
用法：PYTHONPATH="./venv/Lib/site-packages" ./venv/Scripts/python.exe scripts/dev/eval_dialogue/rerank_granularity_ab.py
"""

from __future__ import annotations

import asyncio
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

_env = ROOT / ".env"
if _env.exists():
    load_dotenv(_env)

SEED_PATH = ROOT / "scripts" / "dev" / "eval_dialogue" / "data" / "eval_seed.json"
FETCH_K = 15
TOP_K = 5


def _norm(s: str) -> str:
    return "".join(s.split()).replace("・", "").replace("·", "").replace("、", "").replace("，", "").replace(",", "").replace("—", "")


def _hit_needles(blob: str, variants: dict[str, list[str]]) -> bool:
    return any(any(v and v in blob for v in vlist) for vlist in variants.values())


def _blob_text(block) -> str:
    parts = []
    nt = getattr(block, "narrative_text", None) or ""
    if nt:
        parts.append(nt)
    sc = getattr(block, "scene", None) or ""
    if sc:
        parts.append(sc)
    for d in (getattr(block, "dialogues", None) or []):
        c = getattr(d, "content", None) or ""
        if c:
            parts.append(c)
    return " ".join(parts)


def _ndcg(blobs: list[str], variants: dict[str, list[str]], k: int = TOP_K) -> float:
    def _gain(blob: str) -> float:
        for idx, (_, vlist) in enumerate(variants.items()):
            if any(v and v in blob for v in vlist):
                return max(3.0 - idx, 1.0)
        return 0.0

    gains = [_gain(b) for b in blobs[:k]]
    dcg = sum((2 ** g - 1) / math.log2(i + 2) for i, g in enumerate(gains) if g > 0)
    ideal = sorted([_gain(b) for b in blobs], reverse=True)[:k]
    idcg = sum((2 ** g - 1) / math.log2(i + 2) for i, g in enumerate(ideal) if g > 0)
    return dcg / idcg if idcg > 0 else 0.0


async def main() -> None:
    from src.application.novel.factory import create_novel_retrieval
    from src.infrastructure.reranker import BGEReranker

    seed = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    cases = [c for c in seed["cases"] if c["channel"] == "narrative" and c.get("gold_variants")]

    retrieval = create_novel_retrieval()
    store = retrieval.store
    reranker = BGEReranker(model_path="models/bge-reranker-v2-m3", device="auto", top_n=TOP_K)

    print(f"narrative cases with gold: {len(cases)}", flush=True)

    rows = []
    for ci, case in enumerate(cases, 1):
        q = case["query"]
        variants = case["gold_variants"] or {}
        hits = await store.search(q, channel="narrative", top_k=FETCH_K)
        if not hits:
            print(f"  [{ci}/{len(cases)}] {case['id']}: 0 hits — skip", flush=True)
            continue

        child_blobs, parent_blobs = [], []
        for h in hits:
            child_blobs.append(_blob_text(h.block))
            # parent 邻域：用 expand 逻辑取该 child 的 parent ±1 邻域合并文本
            try:
                from src.domain.novel.narrative_expand import expand_narrative_hits
                ex = expand_narrative_hits(
                    store, [h], radius=1, max_expanded_chars=3500,
                    chapter_hard_boundary=True,
                )
                parent_blobs.append(ex[0].text if ex and ex[0].text else _blob_text(h.block))
            except Exception:
                parent_blobs.append(_blob_text(h.block))

        # rerank 两种输入
        idx_a = await reranker.rerank(q, child_blobs, top_n=TOP_K)
        idx_b = await reranker.rerank(q, parent_blobs, top_n=TOP_K)

        top_a = [child_blobs[i] for i in idx_a[:TOP_K]]
        top_b = [parent_blobs[i] for i in idx_b[:TOP_K]]
        hit_a = int(_hit_needles(" ".join(top_a), variants))
        hit_b = int(_hit_needles(" ".join(top_b), variants))
        ndcg_a = _ndcg(child_blobs, variants)
        ndcg_b = _ndcg(parent_blobs, variants)

        # 输入长度统计
        len_a = round(sum(len(b) for b in child_blobs) / max(1, len(child_blobs)))
        len_b = round(sum(len(b) for b in parent_blobs) / max(1, len(parent_blobs)))

        rows.append({
            "case_id": case["id"], "query": q[:24], "gold": list(variants.keys())[:2],
            "n_cand": len(hits), "child_avg_chars": len_a, "parent_avg_chars": len_b,
            "hit_child": hit_a, "hit_parent": hit_b,
            "ndcg_child": round(ndcg_a, 4), "ndcg_parent": round(ndcg_b, 4),
            "changed": hit_a != hit_b,
        })
        print(
            f"  [{ci}/{len(cases)}] {case['id']:<16} child(hit={hit_a},ndcg={ndcg_a:.3f},avg{len_a}字) "
            f"parent(hit={hit_b},ndcg={ndcg_b:.3f},avg{len_b}字) {'← 变' if hit_a != hit_b else ''}",
            flush=True,
        )

    if not rows:
        print("no rows"); return

    n = len(rows)
    hit_c = sum(r["hit_child"] for r in rows)
    hit_p = sum(r["hit_parent"] for r in rows)
    ndcg_c = sum(r["ndcg_child"] for r in rows) / n
    ndcg_p = sum(r["ndcg_parent"] for r in rows) / n
    changed = sum(1 for r in rows if r["changed"])
    print("\n=== 汇总（narrative 通道，有 gold）===")
    print(f"  case 数: {n}")
    print(f"  hit@5  child输入(150字): {hit_c}/{n} = {hit_c/n:.1%}")
    print(f"  hit@5  parent输入(800字): {hit_p}/{n} = {hit_p/n:.1%}")
    print(f"  ndcg@5 child: {ndcg_c:.4f} | parent: {ndcg_p:.4f}")
    print(f"  排序结果发生变化的 case: {changed}/{n}")

    out = ROOT / "scripts" / "dev" / "eval_dialogue" / "data" / "results" / "rerank_granularity_ab.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n详情: {out}")


if __name__ == "__main__":
    asyncio.run(main())
