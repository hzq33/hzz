"""measure_rerank_input.py — 实测每个 query 实际召回多少条进 rerank。

用 eval_seed 的 query 走生产链路（create_novel_retrieval + search_raw），
在 _maybe_rerank 之前拦截 hits 数量（= 进 rerank 的候选数），并记录
query_variants 数、各通道命中数、rerank 后 top_n。

方法：monkey-patch NovelRetrieval._maybe_rerank，先记录输入 hits 数。
用法：PYTHONPATH="./venv/Lib/site-packages" ./venv/Scripts/python.exe scripts/dev/eval_dialogue/measure_rerank_input.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

_env = ROOT / ".env"
if _env.exists():
    load_dotenv(_env)

SEED_PATH = ROOT / "scripts" / "dev" / "eval_dialogue" / "data" / "eval_seed.json"


async def main() -> None:
    from src.application.novel.factory import create_novel_retrieval
    from src.application.novel.retrieval import NovelRetrieval

    retrieval = create_novel_retrieval()

    # ── patch：在 rerank 前记录候选数 ──
    counts_in: list[int] = []
    counts_out: list[int] = []
    variant_counts: Counter = Counter()
    channel_counts: Counter = Counter()

    orig_rerank = NovelRetrieval._maybe_rerank

    async def patched_rerank(self, query, hits):
        counts_in.append(len(hits))
        for h in hits:
            channel_counts[h.channel] += 1
        out = await orig_rerank(self, query, hits)
        counts_out.append(len(out))
        return out

    NovelRetrieval._maybe_rerank = patched_rerank

    # ── patch：记录 query_variants 数（在 search_raw 的循环处无法直接抓，
    #    用 store.search 计数代理：每次 search 调用即一个变体）──
    seed = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    cases = seed["cases"]

    print(f"评估 query 数: {len(cases)}", flush=True)
    for i, case in enumerate(cases, 1):
        q = case["query"]
        try:
            await retrieval.search_raw(
                q,
                doc_id=case.get("doc_id"),
                available_characters=None,
                doc_ids=case.get("doc_ids") or None,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  [{i}] {q[:20]} ERR {type(exc).__name__}: {exc}", flush=True)
            continue
        if i % 10 == 0 or i == len(cases):
            print(f"  [{i}/{len(cases)}] 累计进rerank: {sum(counts_in)} 条", flush=True)

    NovelRetrieval._maybe_rerank = orig_rerank

    n = len(counts_in)
    if n == 0:
        print("无数据"); return
    s = sorted(counts_in)
    print(f"\n=== 进 rerank 候选数（{n} 个 query）===")
    print(f"  min={s[0]} p25={s[n//4]} median={s[n//2]} p75={s[3*n//4]} max={s[-1]}")
    print(f"  mean={sum(counts_in)/n:.1f}")
    dist = Counter(counts_in)
    print(f"  分布: {dict(sorted(dist.items()))}")
    print(f"  rerank 输出(top_n={retrieval.reranker.top_n}): median={sorted(counts_out)[len(counts_out)//2]}, max={max(counts_out)}")
    print(f"  通道分布: {dict(channel_counts)}")

    out = ROOT / "scripts" / "dev" / "eval_dialogue" / "data" / "results" / "rerank_input_counts.json"
    out.write_text(json.dumps({
        "n": n,
        "counts_in": counts_in,
        "counts_out": counts_out,
        "channel_dist": dict(channel_counts),
        "top_n": retrieval.reranker.top_n,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n结果: {out}")


if __name__ == "__main__":
    asyncio.run(main())
