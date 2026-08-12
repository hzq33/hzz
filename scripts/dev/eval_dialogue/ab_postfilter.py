"""ab_postfilter.py — 角色精确过滤开/关 A/B 验证。

对 eval_seed 每个 query 跑两条链路：
  A. 现状：_apply_character_postfilter 生效
  B. 屏蔽：patch 成 no-op（返回原 hits）

两条链路都走 search_raw → _expand_narrative_context → _format_context，
取最终喂给 LLM 的上下文文本。对比指标：
  - rerank 输入条数（候选宽度）
  - 上下文 gold 命中（gold_variants 子串）
  - 上下文长度
  - 语义重叠（query 与上下文 embedding 余弦）

query_rewriter 固定返回 [query]（避免 LLM 改写引入变量，两组公平）。
用法：PYTHONPATH="./venv/Lib/site-packages" ./venv/Scripts/python.exe scripts/dev/eval_dialogue/ab_postfilter.py
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


def _hit_needles(blob: str, variants: dict[str, list[str]]) -> bool:
    return any(any(v and v in blob for v in vlist) for vlist in variants.values())


async def main() -> None:
    from src.application.novel.factory import create_novel_retrieval
    from src.application.novel.retrieval import NovelRetrieval

    retrieval = create_novel_retrieval()
    store = retrieval.store

    # ── 固定 query 改写为单 query（消除 LLM 变量）──
    class _FixedRewriter:
        async def rewrite(self, query, **kwargs):
            return [query]

    retrieval.query_rewriter = _FixedRewriter()  # type: ignore[assignment]

    seed = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    cases = seed["cases"]

    # ── A/B 各跑一次：用全局开关控制 postfilter 是否 no-op ──
    orig_postfilter = NovelRetrieval._apply_character_postfilter
    disabled = {"flag": False}

    def patched_postfilter(hits, chars):
        if disabled["flag"]:
            return hits
        return orig_postfilter(hits, chars)

    NovelRetrieval._apply_character_postfilter = staticmethod(patched_postfilter)

    async def run_case(q, doc_ids, disable: bool) -> dict:
        disabled["flag"] = disable
        rerank_in = []
        orig_rerank = NovelRetrieval._maybe_rerank

        async def patched_rerank(self, query, hits):
            rerank_in.append(len(hits))
            return await orig_rerank(self, query, hits)

        NovelRetrieval._maybe_rerank = patched_rerank
        try:
            intent, hits = await retrieval.search_raw(
                q, doc_id=None, available_characters=None, doc_ids=doc_ids or None
            )
        finally:
            NovelRetrieval._maybe_rerank = orig_rerank
        hits = retrieval._expand_narrative_context(hits)
        context = retrieval._format_context(q, hits, intent, doc_id=None)
        return {
            "rerank_in": rerank_in[-1] if rerank_in else 0,
            "n_hits": len(hits),
            "context": context,
        }

    rows = []
    for i, case in enumerate(cases, 1):
        q = case["query"]
        doc_ids = case.get("doc_ids") or None
        try:
            a = await run_case(q, doc_ids, disable=False)
            b = await run_case(q, doc_ids, disable=True)
        except Exception as exc:  # noqa: BLE001
            print(f"  [{i}] {case['id']} ERR {type(exc).__name__}: {exc}", flush=True)
            continue

        variants = case.get("gold_variants") or {}
        has_gold = bool(variants)
        gold_a = int(_hit_needles(a["context"], variants)) if has_gold else None
        gold_b = int(_hit_needles(b["context"], variants)) if has_gold else None
        len_a, len_b = len(a["context"]), len(b["context"])
        rows.append({
            "case_id": case["id"], "query": q, "channel": case["channel"],
            "has_gold": has_gold,
            "rerank_in_a": a["rerank_in"], "rerank_in_b": b["rerank_in"],
            "n_hits_a": a["n_hits"], "n_hits_b": b["n_hits"],
            "ctx_len_a": len_a, "ctx_len_b": len_b,
            "gold_a": gold_a, "gold_b": gold_b,
        })
        flag = ""
        if has_gold and gold_a != gold_b:
            flag = " ← gold变" + ("(+)" if gold_b > gold_a else "(-)")
        elif a["rerank_in"] != b["rerank_in"]:
            flag = f" ← rerank_in {a['rerank_in']}→{b['rerank_in']}"
        print(
            f"  [{i}] {case['id']:<16} in {a['rerank_in']:>2}→{b['rerank_in']:>2}  "
            f"gold {gold_a}→{gold_b}  长度 {len_a}→{len_b}{flag}",
            flush=True,
        )

    # ── 汇总 ──
    n = len(rows)
    if n == 0:
        print("无数据"); return
    gold_n = sum(1 for r in rows if r["has_gold"])
    gold_a_hit = sum(1 for r in rows if r["gold_a"] == 1)
    gold_b_hit = sum(1 for r in rows if r["gold_b"] == 1)
    rerank_a = [r["rerank_in_a"] for r in rows]
    rerank_b = [r["rerank_in_b"] for r in rows]
    len_a = sum(r["ctx_len_a"] for r in rows) / n
    len_b = sum(r["ctx_len_b"] for r in rows) / n
    widened = sum(1 for r in rows if r["rerank_in_b"] > r["rerank_in_a"])
    better_gold = sum(1 for r in rows if r["gold_b"] == 1 and r["gold_a"] == 0)
    worse_gold = sum(1 for r in rows if r["gold_a"] == 1 and r["gold_b"] == 0)

    print("\n=== A/B 汇总 ===")
    print(f"  case 数: {n}（gold {gold_n}）")
    print(f"  rerank 输入中位数: A={sorted(rerank_a)[n//2]}  B={sorted(rerank_b)[n//2]}  (B>A: {widened}/{n})")
    print(f"  上下文平均长度: A={len_a:.0f}字  B={len_b:.0f}字")
    print(f"  gold 命中: A={gold_a_hit}/{gold_n}  B={gold_b_hit}/{gold_n}")
    print(f"  gold 改善(B胜A): {better_gold} | gold 恶化(A胜B): {worse_gold}")

    out = ROOT / "scripts" / "dev" / "eval_dialogue" / "data" / "results" / "ab_postfilter.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n详情: {out}")


if __name__ == "__main__":
    asyncio.run(main())
