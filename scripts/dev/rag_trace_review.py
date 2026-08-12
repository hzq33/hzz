#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RAG 检索 trace 复盘 — 真实对话后的检索质量人工评估。

数据源：data/traces/rag_trace.jsonl（NovelRetrieval / store 层自动埋点）。

用法：
    1) 启动服务（trace 默认开启），在前端对话几轮（含 novel 检索）
    2) 运行本脚本查看每次检索的 query / 路由 / scope / 命中原文：

    python scripts/dev/rag_trace_review.py                    # 全部
    python scripts/dev/rag_trace_review.py --q 利姆露          # 按 query 过滤
    python scripts/dev/rag_trace_review.py --kind novel_retrieval
    python scripts/dev/rag_trace_review.py --limit 10 --out review.md
    python scripts/dev/rag_trace_review.py --json > review.json

    3) 人工标注（可选）：生成可编辑评分表，逐条填 y/n + 备注，
       再汇总命中率：

    python scripts/dev/rag_trace_review.py --annotate > annotate.csv
    # 编辑 annotate.csv 的 relevant 列（y/n），然后：
    python scripts/dev/rag_trace_review.py --summarize annotate.csv

输出：stdout（默认）或 --out 文件（markdown 报告）。
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

TRACE_PATH = Path(__file__).resolve().parents[2] / "data" / "traces" / "rag_trace.jsonl"

CHANNEL_LABEL = {
    "narrative": "叙事",
    "dialogue": "对话",
    "qa": "QA",
    "character": "角色",
    "unknown": "?",
}


def load_traces(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with path.open(encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def filter_traces(traces: list[dict], args) -> list[dict]:
    out = []
    for t in traces:
        if args.kind and t.get("kind") != args.kind:
            continue
        if args.channel and t.get("channel") != args.channel:
            continue
        if args.q:
            q = (t.get("query") or "").lower()
            if args.q.lower() not in q:
                continue
        if args.session:
            sid = t.get("session_id") or ""
            if args.session not in sid:
                continue
        if args.no_session and t.get("session_id"):
            continue
        if args.zero_only and not t.get("zero_hit"):
            continue
        out.append(t)
    return out


def overview(traces: list[dict]) -> str:
    n = len(traces)
    lines = [f"### 概览（共 {n} 条检索记录）", ""]
    if not n:
        return "\n".join(lines)
    kinds = Counter(t.get("kind") for t in traces)
    lines.append(f"- kind: {', '.join(f'{k}={v}' for k, v in kinds.most_common())}")
    channels = Counter((t.get("channel") or t.get("primary_channel") or "?").split(",")[0] for t in traces)
    ch_str = ", ".join(f"{CHANNEL_LABEL.get(c, c)}={v}" for c, v in channels.most_common())
    lines.append(f"- 通道: {ch_str}")
    zero = sum(1 for t in traces if t.get("zero_hit"))
    lines.append(f"- **零命中: {zero}（{zero / n:.0%}）**")
    # scope 覆盖率（doc_id / series 锁定）
    scoped = sum(
        1 for t in traces
        if t.get("doc_id") or (t.get("filters") or {}).get("series") or (t.get("filters") or {}).get("doc_ids")
    )
    lines.append(f"- 检索范围锁定（doc_id/series/doc_ids）: {scoped}（{scoped / n:.0%}）")
    hits = [len(t.get("hits") or []) for t in traces]
    if hits:
        lines.append(f"- 平均命中数: {sum(hits) / len(hits):.1f}")
    times = [t.get("elapsed_ms") or 0 for t in traces if t.get("elapsed_ms")]
    if times:
        lines.append(f"- 平均耗时: {sum(times) / len(times):.0f}ms")
    variants = [t.get("query_variants") or 1 for t in traces if t.get("query_variants")]
    if variants:
        lines.append(f"- query 改写变体数均值: {sum(variants) / len(variants):.1f}")
    return "\n".join(lines)


def one_trace_detail(t: dict, idx: int, judge: dict | None = None) -> str:
    q = (t.get("query") or "").strip()[:120]
    kind = t.get("kind", "")
    channel = t.get("channel") or t.get("primary_channel") or "?"
    lines = [f"#### {idx}. [{kind}] 通道={CHANNEL_LABEL.get(channel, channel)}  `{q}`"]
    meta = []
    if judge and judge.get("score") is not None:
        meta.append(f"**Judge {judge['score']:.2f}**")
    if t.get("session_id"):
        meta.append(f"会话={t['session_id']}")
    if t.get("query_variants"):
        meta.append(f"变体×{t['query_variants']}")
    if t.get("elapsed_ms"):
        meta.append(f"{t['elapsed_ms']}ms")
    if t.get("zero_hit"):
        meta.append("**零命中**")
    if meta:
        lines.append("> " + " · ".join(meta))
    if judge and judge.get("reason"):
        lines.append(f"> Judge 理由：{judge['reason']}")
    # scope / 路由
    scope = []
    if t.get("doc_id"):
        scope.append(f"doc_id={t['doc_id']}")
    if t.get("series_id"):
        scope.append(f"series={t['series_id']}")
    if t.get("resolved_entities"):
        scope.append("实体=" + "、".join(t["resolved_entities"][:4]))
    if t.get("target_characters"):
        scope.append("目标=" + "、".join(t["target_characters"][:4]))
    if t.get("filters"):
        f = t["filters"]
        if f.get("characters"):
            scope.append("角色过滤=" + "、".join(f["characters"][:4]))
    if scope:
        lines.append("")
        lines.append("**范围**：" + "；".join(scope))
    # 命中
    hits = t.get("hits") or []
    if hits:
        lines.append("")
        lines.append("| # | 类型 | doc_id / 章节 | 相关度 | 原文预览 |")
        lines.append("|---|------|--------------|--------|----------|")
        for i, h in enumerate(hits[:6], 1):
            doc = h.get("doc_id") or ""
            ch = (h.get("chapter_title") or "")[:18]
            loc = f"{doc[:24]}" + (f" / {ch}" if ch else "")
            preview = (h.get("preview") or "").replace("|", "\\|")[:90]
            lines.append(
                f"| {i} | {CHANNEL_LABEL.get(h.get('block_type'), h.get('block_type'))} "
                f"| {loc} | {h.get('score')} | {preview} |"
            )
    else:
        lines.append("")
        lines.append("_零命中——无相关内容返回_")
    lines.append("")
    return "\n".join(lines)


def build_report(traces: list[dict], judge_results: list[dict] | None = None) -> str:
    lines = ["# RAG 检索复盘报告", "", f"_生成时间：{__import__('datetime').datetime.now().isoformat(timespec='seconds')}_", ""]
    lines.append(overview(traces))
    lines.append("")
    if judge_results:
        lines.append(build_judge_section(judge_results))
        lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 逐条详情")
    lines.append("")
    for i, t in enumerate(traces, 1):
        jr = judge_results[i - 1] if judge_results and i <= len(judge_results) else None
        lines.append(one_trace_detail(t, i, judge=jr))
    return "\n".join(lines)


def build_judge_section(judge_results: list[dict]) -> str:
    """LLM judge 评分汇总：分数分布 + 低分 top-N（人工确认）。"""
    scores = [r["score"] for r in judge_results if r.get("score") is not None]
    if not scores:
        return "### LLM Judge\n\n_无有效评分（可能缺 DEEPSEEK_API_KEY 或全部失败）_"
    avg = sum(scores) / len(scores)
    low = [r for r in judge_results if r.get("score") is not None and r["score"] < 0.5]
    high = [r for r in judge_results if r.get("score") is not None and r["score"] >= 0.7]
    lines = [
        "### LLM Judge 评分（DeepSeek 相关性 0-1）",
        "",
        f"- **平均分: {avg:.3f}**（{len(scores)} 条有效）",
        f"- 高分(≥0.7): {len(high)} · 低分(<0.5): {len(low)}",
        "",
    ]
    if low:
        lines.append("#### ⚠️ 低分项（<0.5，建议人工确认）")
        lines.append("")
        for r in sorted(low, key=lambda x: x["score"]):
            lines.append(f"- **{r['score']:.2f}** `{r['query'][:80]}` [{r['channel']}]")
            lines.append(f"  - 理由：{r.get('reason', '')}")
            if r.get("preview"):
                lines.append(f"  - 命中：{r['preview'][:120]}")
        lines.append("")
    else:
        lines.append("_无低分项_")
        lines.append("")
    return "\n".join(lines)


def run_llm_judge(traces: list[dict], *, limit: int = 0, concurrency: int = 4) -> list[dict]:
    """对每条非零命中检索调 DeepSeek 打分（并发）。返回逐条结果。"""
    targets = [t for t in traces if t.get("hits")]
    if limit > 0:
        targets = targets[:limit]
    if not targets:
        return []

    # 复用项目已有 judge_self.judge_relevance（DeepSeek 相关性 0-1）
    try:
        from scripts.dev.eval_dialogue.judge_self import judge_relevance
    except ImportError:
        print("错误：无法导入 judge_self（需要 scripts/dev/eval_dialogue/）", file=sys.stderr)
        return []

    def _judge(t: dict) -> dict:
        q = (t.get("query") or "").strip()
        contexts = [
            (h.get("preview") or "")[:300]
            for h in (t.get("hits") or [])[:5]
            if (h.get("preview") or "").strip()
        ]
        if not q or not contexts:
            return {"query": q, "channel": t.get("channel") or t.get("primary_channel") or "",
                    "score": 0.0, "reason": "无上下文", "preview": ""}
        character = ""
        if t.get("target_characters"):
            character = t["target_characters"][0]
        try:
            r = judge_relevance(q, contexts, character=character)
        except Exception as exc:  # noqa: BLE001
            return {"query": q, "channel": t.get("channel") or t.get("primary_channel") or "",
                    "score": None, "reason": f"judge 调用失败: {exc}", "preview": ""}
        return {
            "query": q,
            "channel": t.get("channel") or t.get("primary_channel") or "",
            "score": r.get("score"),
            "reason": r.get("reason", ""),
            "preview": contexts[0] if contexts else "",
        }

    # 同步 judge（judge_self 是同步 OpenAI 调用），用线程池并发
    from concurrent.futures import ThreadPoolExecutor

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
        results = list(ex.map(_judge, targets))
    return results


def build_annotate_csv(traces: list[dict]) -> str:
    buf = []
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["id", "ts", "kind", "channel", "query", "doc_id", "hit_count", "relevant", "note"])
    for i, t in enumerate(traces, 1):
        w.writerow([
            i,
            t.get("ts", ""),
            t.get("kind", ""),
            t.get("channel") or t.get("primary_channel") or "",
            (t.get("query") or "")[:100],
            t.get("doc_id") or "",
            len(t.get("hits") or []),
            "",  # relevant: 人工填 y/n
            "",
        ])
    return "".join(buf)


def summarize_annotations(path: Path) -> str:
    rows = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            rel = (r.get("relevant") or "").strip().lower()
            if rel in {"y", "yes", "1", "true"}:
                rows.append((True, r))
            elif rel in {"n", "no", "0", "false"}:
                rows.append((False, r))
    total = len(rows)
    if not total:
        return "标注文件无有效行（relevant 填 y 或 n）。"
    hits = sum(1 for ok, _ in rows if ok)
    lines = [
        f"# 人工标注汇总（{total} 条已标注）",
        "",
        f"- **相关命中率: {hits}/{total} = {hits / total:.0%}**",
        "",
        "## 判定为不相关（relevant=n）",
        "",
    ]
    for ok, r in rows:
        if ok:
            continue
        lines.append(f"- `{r.get('query', '')}` [{r.get('channel', '')}] {r.get('note', '')}")
    lines.append("")
    lines.append("## 判定为相关（relevant=y）")
    lines.append("")
    for ok, r in rows:
        if not ok:
            continue
        lines.append(f"- `{r.get('query', '')}` [{r.get('channel', '')}]")
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser(description="RAG 检索 trace 复盘")
    p.add_argument("--trace", default=str(TRACE_PATH), help="trace jsonl 路径")
    p.add_argument("--kind", default=None, help="novel_retrieval | store_search")
    p.add_argument("--channel", default=None, help="narrative|dialogue|qa|character")
    p.add_argument("--q", default=None, help="query 子串过滤")
    p.add_argument("--session", default=None, help="session_id 子串过滤（如 imp_xxx）")
    p.add_argument("--no-session", action="store_true", help="只看无会话归属的记录（后台任务等）")
    p.add_argument("--zero-only", action="store_true", help="只看零命中")
    p.add_argument("--limit", type=int, default=0, help="最多输出 N 条")
    p.add_argument("--out", default=None, help="报告输出文件")
    p.add_argument("--json", action="store_true", help="输出原始 JSON（过滤后）")
    p.add_argument("--annotate", action="store_true", help="输出可编辑标注 CSV")
    p.add_argument("--summarize", metavar="CSV", default=None, help="汇总已标注 CSV")
    p.add_argument("--llm-judge", action="store_true", help="用 DeepSeek 对每条检索自动打分（需要 DEEPSEEK_API_KEY）")
    p.add_argument("--judge-limit", type=int, default=0, help="LLM judge 最多评估 N 条（省 token，默认全部）")
    p.add_argument("--judge-concurrency", type=int, default=4, help="LLM judge 并发数")
    args = p.parse_args()

    if args.summarize:
        print(summarize_annotations(Path(args.summarize)))
        return

    traces = filter_traces(load_traces(Path(args.trace)), args)
    if args.limit > 0:
        traces = traces[: args.limit]

    judge_results = None
    if args.llm_judge:
        judge_results = run_llm_judge(
            traces,
            limit=args.judge_limit,
            concurrency=args.judge_concurrency,
        )

    if args.annotate:
        out = build_annotate_csv(traces)
    elif args.json:
        out = json.dumps(traces, ensure_ascii=False, indent=2)
    else:
        out = build_report(traces, judge_results=judge_results)

    if args.out:
        Path(args.out).write_text(out, encoding="utf-8")
        print(f"报告已写入: {args.out}")
    else:
        print(out)


if __name__ == "__main__":
    main()
