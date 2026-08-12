"""评估生产链路（search_raw，含 rewrite + routing + rerank）的真实相关性。

对 eval_seed 每条 case：
  1. search_raw(query) → 生产 top-5（LLM 改写 + LLM 路由 + hybrid + BGE rerank）
  2. judge_self 打分（DeepSeek 相关性 0-1）
输出均分 + 逐 case 明细。

用法：
    PYTHONPATH=".;./venv/Lib/site-packages" ./venv/Scripts/python.exe scripts/dev/eval_dialogue/judge_routed.py
"""
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

SEED = ROOT / "scripts" / "dev" / "eval_dialogue" / "data" / "eval_seed.json"
OUT = ROOT / "scripts" / "dev" / "eval_dialogue" / "data" / "judge_results" / "routed_judge.json"


def _render(block, limit: int = 300) -> str:
    parts = [block.narrative_text or ""]
    parts += [f"{d.speaker}:{d.content}" for d in (block.dialogues or [])[:4]]
    text = " ".join(p for p in parts if p).strip()
    return text[:limit]


async def main() -> None:
    from src.application.novel.factory import create_novel_retrieval
    from scripts.dev.eval_dialogue.judge_self import judge_relevance

    seed = json.loads(SEED.read_text(encoding="utf-8"))
    cases = seed["cases"]
    retrieval = create_novel_retrieval()
    chars = retrieval.store.list_characters() or None
    print(f"评估 {len(cases)} case（生产链路：rewrite→route→hybrid→rerank）…")

    rows = []
    for i, case in enumerate(cases, 1):
        q = case["query"]
        intent, hits = await retrieval.search_raw(q, available_characters=chars)
        ctxs = [_render(h.block) for h in hits[:5]]
        result = judge_relevance(q, ctxs, character=case.get("character", ""))
        rows.append({
            "case_id": case["id"],
            "query": q,
            "channel": case["channel"],
            "character": case.get("character", ""),
            "routed_channel": intent.primary_channel,
            "hit_count": len(hits),
            "self_score": result.get("score"),
            "self_reason": result.get("reason", ""),
        })
        if i % 10 == 0 or i == len(cases):
            done = [r for r in rows if r["self_score"] is not None]
            avg = sum(r["self_score"] for r in done) / len(done) if done else 0
            print(f"  [{i}/{len(cases)}] 当前均分 {avg:.3f}")

    scores = [r["self_score"] for r in rows if r["self_score"] is not None]
    avg = sum(scores) / len(scores) if scores else 0
    print(f"\n=== 生产链路 self judge 均分: {avg:.3f} ({len(scores)}/{len(cases)}) ===")

    # 低分分布
    low = [r for r in rows if r["self_score"] is not None and r["self_score"] < 0.5]
    print(f"低分 (<0.5): {len(low)}")
    for r in sorted(low, key=lambda x: x["self_score"])[:15]:
        print(f"  {r['case_id']:<14} {r['self_score']:.2f} | {r['query'][:28]} | {(r['self_reason'] or '')[:50]}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"avg": avg, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n落盘: {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
