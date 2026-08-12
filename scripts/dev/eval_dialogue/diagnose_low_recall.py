"""diagnose_low_recall.py — 深挖进 rerank ≤3 条的低召回 query。

对 eval_seed 每个 query 走 search_raw，patch 各环节记录：
  - store.search / search_multi 的 (channel, top_k, filters) 与返回条数
  - _apply_character_postfilter 前后条数（rerank 前最后的削量点）
  - _maybe_rerank 输入条数
对低召回（进 rerank ≤3）的 query：
  - 复跑"无 filters 全库检索"对照：区分 filter 挡 vs 库内本无
  - 打印 filters 详情

用法：PYTHONPATH="./venv/Lib/site-packages" ./venv/Scripts/python.exe scripts/dev/eval_dialogue/diagnose_low_recall.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

_env = ROOT / ".env"
if _env.exists():
    load_dotenv(_env)

SEED_PATH = ROOT / "scripts" / "dev" / "eval_dialogue" / "data" / "eval_seed.json"
LOW_THRESHOLD = 3


async def main() -> None:
    from src.application.novel.factory import create_novel_retrieval
    from src.application.novel.retrieval import NovelRetrieval

    retrieval = create_novel_retrieval()
    store = retrieval.store

    # ── 记录 search 调用与 postfilter ──
    search_calls: list[dict] = []

    # HierarchicalNovelStore.search 转发到 _vectors；patch 内层向量 store
    vectors = getattr(store, "_vectors", store)
    orig_search = vectors.search
    orig_search_multi = vectors.search_multi

    async def patched_search(query, channel="narrative", top_k=5, doc_id=None, filters=None, min_score=None):
        hits = await orig_search(query, channel=channel, top_k=top_k, doc_id=doc_id, filters=filters, min_score=min_score)
        search_calls.append({
            "kind": "search", "channel": channel, "top_k": top_k,
            "doc_id": doc_id, "filters": filters, "min_score": min_score, "n": len(hits),
        })
        return hits

    async def patched_search_multi(query, channel_weights, doc_id=None, top_k=5, filters=None, min_score=None):
        hits = await orig_search_multi(query, channel_weights, doc_id=doc_id, top_k=top_k, filters=filters, min_score=min_score)
        search_calls.append({
            "kind": "search_multi", "channels": sorted(channel_weights.keys()),
            "top_k": top_k, "doc_id": doc_id, "filters": filters, "min_score": min_score, "n": len(hits),
        })
        return hits

    vectors.search = patched_search
    vectors.search_multi = patched_search_multi

    orig_postfilter = NovelRetrieval._apply_character_postfilter
    postfilter_log: list[dict] = []

    def patched_postfilter(hits, chars):
        before = len(hits)
        out = orig_postfilter(hits, chars)
        postfilter_log.append({"chars": chars, "before": before, "after": len(out)})
        return out

    NovelRetrieval._apply_character_postfilter = staticmethod(patched_postfilter)

    orig_rerank = NovelRetrieval._maybe_rerank
    rerank_in: list[int] = []

    async def patched_rerank(self, query, hits):
        rerank_in.append(len(hits))
        return await orig_rerank(self, query, hits)

    NovelRetrieval._maybe_rerank = patched_rerank

    seed = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    cases = seed["cases"]

    diagnostics: list[dict] = []
    for i, case in enumerate(cases, 1):
        q = case["query"]
        search_calls.clear()
        postfilter_log.clear()
        rerank_in.clear()
        try:
            await retrieval.search_raw(
                q,
                doc_id=case.get("doc_id"),
                available_characters=None,
                doc_ids=case.get("doc_ids") or None,
            )
        except Exception as exc:  # noqa: BLE001
            import traceback
            print(f"  [{i}] {case['id']} ERR {type(exc).__name__}: {exc}", flush=True)
            traceback.print_exc(limit=3)
            continue

        n_in = rerank_in[-1] if rerank_in else 0
        if n_in <= LOW_THRESHOLD:
            # 低召回 → 对照：无 filter 全库检索
            ch = case["channel"]
            try:
                free_hits = await orig_search(q, channel=ch, top_k=15)
                free_n = len(free_hits)
            except Exception:
                free_n = -1
            diag = {
                "case_id": case["id"],
                "query": q,
                "channel": case["channel"],
                "intent": case.get("intent"),
                "character": case.get("character"),
                "gold": list((case.get("gold_variants") or {}).keys())[:3],
                "rerank_in": n_in,
                "free_search_n": free_n,       # 无 filter 全库能召回多少
                "searches": list(search_calls),
                "postfilters": list(postfilter_log),
            }
            diagnostics.append(diag)
            print(
                f"[{i}] {case['id']} rerank_in={n_in} 无filter召回={free_n} "
                f"ch={case['channel']} | {q[:22]}",
                flush=True,
            )
            for sc in search_calls:
                print(f"      search {sc['channel']} top_k={sc['top_k']} n={sc['n']} "
                      f"doc_id={sc['doc_id']} filters={json.dumps(sc['filters'], ensure_ascii=False)[:120]}", flush=True)
            for pf in postfilter_log:
                print(f"      postfilter chars={pf['chars']} {pf['before']}→{pf['after']}", flush=True)

    # 还原
    vectors.search = orig_search
    vectors.search_multi = orig_search_multi
    NovelRetrieval._apply_character_postfilter = orig_postfilter
    NovelRetrieval._maybe_rerank = orig_rerank

    print(f"\n=== 低召回 query 汇总（进 rerank ≤ {LOW_THRESHOLD}）: {len(diagnostics)} 个 ===")
    for d in diagnostics:
        tag = "库内无内容" if d["free_search_n"] < 5 else f"filter挡掉(无filter={d['free_search_n']})"
        print(f"  {d['case_id']:<16} in={d['rerank_in']} 无filter={d['free_search_n']:<3} {tag} | {d['query'][:26]}")

    out = ROOT / "scripts" / "dev" / "eval_dialogue" / "data" / "results" / "low_recall_diagnosis.json"
    out.write_text(json.dumps(diagnostics, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n详情: {out}")


if __name__ == "__main__":
    asyncio.run(main())
