# -*- coding: utf-8 -*-
"""端到端在线评估质量验证（临时诊断脚本，非 CI）

从真实 trace 抽样 query → 生产检索链路复现 → BGE 客观相关性 → LLM judge 交叉验证。
输出 data/traces/eval_quality_report.json（UTF-8），供人工复核检索质量。
"""
import asyncio
import json
import os
import random
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
from dotenv import load_dotenv

load_dotenv(ROOT / ".env", encoding="utf-8-sig")

TRACE_FILE = ROOT / "data" / "traces" / "rag_trace.jsonl"
OUT_FILE = ROOT / "data" / "traces" / "eval_quality_report.json"
SAMPLE_PER_CHANNEL = 4          # 每通道抽样数
JUDGE_N = 8                     # LLM judge 抽样条数


def load_traces():
    out = []
    with open(TRACE_FILE, encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def sample_queries(traces):
    """按通道分层抽样，query 去重，优先带零命中与多样 doc。"""
    by_channel = defaultdict(list)
    seen_q = set()
    for t in traces:
        ch = t.get("channel") or t.get("primary_channel") or "unknown"
        q = (t.get("query") or "").strip()
        if not q or q in seen_q or len(q) > 80:
            continue
        seen_q.add(q)
        by_channel[ch].append(t)
    sampled = []
    for ch in ("narrative", "dialogue", "character", "qa", "unknown"):
        pool = by_channel.get(ch, [])
        random.Random(42).shuffle(pool)
        sampled.extend(pool[:SAMPLE_PER_CHANNEL])
    return sampled


async def reproduce_search(store, t):
    """复现生产 store 检索（hybrid keyword+vector，与线上同一 store 路径）。"""
    q = (t.get("query") or "").strip()
    ch = t.get("channel") or t.get("primary_channel") or "narrative"
    filters = dict(t.get("filters") or {})
    doc_id = t.get("doc_id") or None
    hits = await store.search(q, channel=ch, doc_id=doc_id, top_k=5, filters=filters or None)
    return [{
        "global_id": h.block.global_id,
        "block_type": h.block.block_type,
        "chapter_title": h.block.chapter_title or "",
        "score": round(float(h.score or 0), 4),
        "text": (
            (h.block.narrative_text or "")
            or " ".join(d.content for d in (h.block.dialogues or []))
            or (h.block.question or "")
            or ""
        )[:300],
    } for h in hits]


async def main():
    t0 = time.perf_counter()
    traces = load_traces()
    print(f"[1/5] 读取 trace: {len(traces)} 条")

    samples = sample_queries(traces)
    print(f"[2/5] 抽样 query: {len(samples)} 条")
    for s in samples[:15]:
        print(f"   - [{s.get('channel') or s.get('primary_channel')}] {s['query'][:40]}")

    from src.application.novel.factory import create_novel_store
    from src.infrastructure.reranker import resolve_reranker
    store = create_novel_store()
    print(f"[3/5] store 就绪: {store.block_count()} blocks, {len(store.doc_ids())} docs")

    # BGE reranker（本地客观基线）
    reranker = None
    try:
        reranker = resolve_reranker(enabled=True, provider="bge", top_n=5)
        print("   BGE reranker 就绪:", type(reranker).__name__)
    except Exception as e:
        print(f"   BGE reranker 不可用: {e}")

    # ── 复现检索 + BGE 打分 ──
    cases = []
    for i, t in enumerate(samples, 1):
        q = (t.get("query") or "").strip()
        hits = await reproduce_search(store, t)
        bge_scores = []
        if reranker is not None and hits:
            docs = [h["text"] or h["global_id"] for h in hits]
            try:
                loader = getattr(reranker, "_load_model", None)
                if loader is not None:
                    loader()
                scores = reranker._score_batch(q, docs)
                bge_scores = [round(float(s), 4) for s in scores]
            except Exception as e:
                print(f"   BGE 打分失败 {q[:20]}: {e}")
        cases.append({
            "query": q,
            "channel": t.get("channel") or t.get("primary_channel") or "",
            "trace_ts": t.get("ts", ""),
            "trace_zero_hit": bool(t.get("zero_hit")),
            "repro_hits": hits,
            "bge_scores": bge_scores,
            "bge_max": max(bge_scores) if bge_scores else None,
        })
        if i % 5 == 0:
            print(f"   检索复现 {i}/{len(samples)} …")

    # ── LLM judge（真实 DeepSeek）──
    judged = []
    targets = [c for c in cases if c["repro_hits"]][:JUDGE_N]
    if targets:
        from scripts.dev.eval_dialogue.judge_self import judge_relevance

        def _judge(c):
            try:
                r = judge_relevance(c["query"], [h["text"] for h in c["repro_hits"]])
                return {**r, "query": c["query"], "bge_max": c["bge_max"]}
            except Exception as e:
                return {"query": c["query"], "score": None, "reason": f"judge 失败: {e}", "bge_max": c["bge_max"]}

        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=3) as ex:
            judged = list(ex.map(_judge, targets))
        print(f"[4/5] LLM judge: {len(judged)} 条完成")

    # ── 质量统计 ──
    stats = {
        "repro_zero_hit_rate": round(sum(1 for c in cases if not c["repro_hits"]) / len(cases), 3),
        "trace_zero_hit_rate": round(sum(1 for c in cases if c["trace_zero_hit"]) / len(cases), 3),
        "bge_scored": len([c for c in cases if c["bge_max"] is not None]),
        "bge_mean_max": round(sum(c["bge_max"] or 0 for c in cases if c["bge_max"] is not None)
                              / max(1, len([c for c in cases if c["bge_max"] is not None])), 3),
        "bge_low_lt_04": len([c for c in cases if c["bge_max"] is not None and c["bge_max"] < 0.4]),
    }
    if judged:
        scored = [j for j in judged if j.get("score") is not None]
        stats["judge_count"] = len(scored)
        stats["judge_mean"] = round(sum(j["score"] for j in scored) / max(1, len(scored)), 3)
        stats["judge_low_lt_05"] = len([j for j in scored if j["score"] < 0.5])
        # judge vs BGE 一致性（同 query 方向对比）
        pairs = [(j["score"], j.get("bge_max")) for j in scored if j.get("bge_max") is not None]
        if len(pairs) >= 3:
            agree = sum(1 for js, bs in pairs if (js >= 0.5) == (bs >= 0.5))
            stats["judge_bge_agreement"] = round(agree / len(pairs), 3)
            stats["judge_bge_pairs"] = len(pairs)

    report = {"sampled": len(cases), "stats": stats, "cases": cases, "judged": judged}
    OUT_FILE.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[5/5] 报告写出: {OUT_FILE} ({time.perf_counter() - t0:.0f}s)")
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
