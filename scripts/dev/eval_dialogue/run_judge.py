"""run_judge.py — L3：三层 LLM judge 对比验证。

读 L2 结果缓存 → 对每条 case 跑：
  - 自写 judge（judge_self）：相关性 0-1（第一性指标，全部 case）
  - RAGAS context_precision（judge_ragas）：仅有关键词 gold 的 case
  - DeepEval contextual_precision（judge_deepeval）：仅有关键词 gold 的 case

输出：
  - 逐 case 对照表（proxy / self / ragas / deepeval）
  - 一致性分析（self vs proxy 同向率、ragas/deepeval vs self 相关性）
  - 落盘 judge 结果 JSON

用法：
    EVAL_LLM_JUDGE=1 PYTHONPATH="./venv/Lib/site-packages" ./venv/Scripts/python.exe scripts/dev/eval_dialogue/run_judge.py [--limit N] [--results <path>]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

_env = ROOT / ".env"
if _env.exists():
    load_dotenv(_env)

RESULT_DIR = ROOT / "scripts" / "dev" / "eval_dialogue" / "data" / "results"
JUDGE_OUT = ROOT / "scripts" / "dev" / "eval_dialogue" / "data" / "judge_results"

PROXY_THRESHOLD = 0.5  # proxy 二值化阈值


def _ctx_texts(entry: dict, limit: int = 5) -> list[str]:
    """作品内 top-k 块渲染文本（judge 输入）。"""
    out = []
    for h in entry.get("channel_search", {}).get("in_doc", [])[:limit]:
        b = h["block"]
        parts = [b["text"]]
        parts += [f"{d['speaker']}:{d['content']}" for d in b.get("dialogues", [])[:4]]
        out.append(" ".join(p for p in parts if p)[:400])
    return out


def _gold_text(entry: dict) -> str:
    kws = entry.get("gold_keywords") or []
    variants = entry.get("gold_variants") or {}
    # reference = 规范名 + 主要变体，供 with-reference 指标使用
    parts = []
    for kw in kws:
        parts.append(kw)
        parts += (variants.get(kw) or [])[:3]
    return " ".join(dict.fromkeys(parts))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 条（小样验证）")
    ap.add_argument("--results", type=str, default="", help="L2 结果文件路径（默认最新）")
    args = ap.parse_args()

    if not os.getenv("EVAL_LLM_JUDGE", "").strip().lower() in {"1", "true", "yes", "on"}:
        print("[ABORT] 需要 EVAL_LLM_JUDGE=1 才运行 LLM judge（烧 DeepSeek token）")
        sys.exit(1)

    results_path = Path(args.results) if args.results else sorted(RESULT_DIR.glob("*_results*.json"))[-1]
    data = json.loads(results_path.read_text(encoding="utf-8"))
    entries = data["results"]
    if args.limit:
        entries = entries[: args.limit]
    print(f"加载结果：{results_path.name} → {len(entries)} case（judge 输入 = 作品内 top-5 块）")

    from judge_self import judge_relevance
    from judge_ragas import judge_one as ragas_one
    from judge_deepeval import judge_one as deepeval_one

    rows = []
    # 并发执行（DeepSeek API 支持并发；ragas/deepeval metric 均 thread-local 缓存）
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def judge_entry(e: dict) -> dict:
        q = e["query"]
        char = e.get("character") or ""
        ctxs = _ctx_texts(e)
        gold = _gold_text(e)
        has_gold = bool(e.get("gold_keywords"))
        proxy = e["metrics"].get("indoc_hit_at_5")
        proxy_bin = 1 if proxy is not None and proxy >= PROXY_THRESHOLD else (None if proxy is None else 0)

        self_r = judge_relevance(q, ctxs, char)
        ragas_s = ragas_one(q, ctxs, gold) if (has_gold and ctxs) else None
        deepeval_s = deepeval_one(q, ctxs, gold) if (has_gold and ctxs) else None

        return {
            "case_id": e["case_id"],
            "query": q,
            "character": char,
            "channel": e["channel"],
            "has_gold": has_gold,
            "proxy_indoc_hit": proxy,
            "self_score": self_r["score"],
            "self_reason": self_r["reason"],
            "ragas_ctx_prec": ragas_s,
            "deepeval_ctx_prec": deepeval_s,
        }

    done = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(judge_entry, e): e for e in entries}
        for fut in as_completed(futures):
            e = futures[fut]
            try:
                row = fut.result()
            except Exception as exc:  # noqa: BLE001
                row = {
                    "case_id": e["case_id"], "query": e["query"], "character": e.get("character") or "",
                    "channel": e["channel"], "has_gold": bool(e.get("gold_keywords")),
                    "proxy_indoc_hit": e["metrics"].get("indoc_hit_at_5"),
                    "self_score": None, "self_reason": f"entry error: {exc}", "ragas_ctx_prec": None, "deepeval_ctx_prec": None,
                }
            rows.append(row)
            done += 1
            if done % 10 == 0 or done == len(entries):
                print(f"  [{done}/{len(entries)}] {row['case_id']} proxy={row['proxy_indoc_hit']} self={row['self_score']} ragas={row['ragas_ctx_prec'] if row['ragas_ctx_prec'] is None else round(row['ragas_ctx_prec'],2)} deepeval={row['deepeval_ctx_prec'] if row['deepeval_ctx_prec'] is None else round(row['deepeval_ctx_prec'],2)} | {row['query'][:24]}")

    # ── 一致性分析 ──
    print("\n=== 一致性分析 ===")

    def corr(a_key: str, b_key: str):
        pairs = [(r[a_key], r[b_key]) for r in rows if r[a_key] is not None and r[b_key] is not None]
        if not pairs:
            return None, 0
        # 同向：都 >= 0.5 或都 < 0.5
        agree = sum(1 for a, b in pairs if (a >= PROXY_THRESHOLD) == (b >= PROXY_THRESHOLD))
        return agree / len(pairs), len(pairs)

    for a, b, label in [
        ("self_score", "proxy_indoc_hit", "self judge vs 代理(作品内top5命中)"),
        ("ragas_ctx_prec", "self_score", "RAGAS vs self judge"),
        ("deepeval_ctx_prec", "self_score", "DeepEval vs self judge"),
        ("ragas_ctx_prec", "deepeval_ctx_prec", "RAGAS vs DeepEval"),
    ]:
        rate, n = corr(a, b)
        print(f"  同向率 {label}: {rate if rate is None else f'{rate:.1%}'} ({n} case)")

    # 平均分
    for key, label in [("self_score", "self judge"), ("ragas_ctx_prec", "RAGAS"), ("deepeval_ctx_prec", "DeepEval")]:
        vals = [r[key] for r in rows if r[key] is not None]
        if vals:
            print(f"  {label} 均分: {sum(vals)/len(vals):.3f} ({len(vals)} case)")

    # 分歧 case（self 与 proxy 不一致）
    print("\n=== self vs proxy 分歧 case ===")
    for r in rows:
        if r["proxy_indoc_hit"] is None or r["self_score"] is None:
            continue
        if (r["self_score"] >= PROXY_THRESHOLD) != (r["proxy_indoc_hit"] >= PROXY_THRESHOLD):
            print(f"  {r['case_id']:<14} proxy={r['proxy_indoc_hit']} self={r['self_score']} | {r['query'][:30]}")
            print(f"      reason: {r['self_reason'][:80]}")

    JUDGE_OUT.mkdir(parents=True, exist_ok=True)
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out = JUDGE_OUT / f"{ts}_judge.json"
    out.write_text(
        json.dumps(
            {"meta": {"source": results_path.name, "limit": args.limit, "timestamp": ts},
             "rows": rows},
            ensure_ascii=False, indent=1,
        ),
        encoding="utf-8",
    )
    print(f"\njudge 结果：{out}")


if __name__ == "__main__":
    main()
