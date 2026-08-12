"""report.py — L4：评估报告生成（Markdown + JSON）+ baseline diff。

输入：L2 结果文件（results/*_results*.json）+ L3 judge 结果文件（judge_results/*_judge.json）
输出：docs/analysis/dialogue_eval/YYYY-MM-DD_<sha>.md + .json，并更新 baseline/dialogue_eval_latest.json

用法：
    PYTHONPATH="./venv/Lib/site-packages" ./venv/Scripts/python.exe scripts/dev/eval_dialogue/report.py [--results <path>] [--judge <path>]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

RESULT_DIR = ROOT / "scripts" / "dev" / "eval_dialogue" / "data" / "results"
JUDGE_DIR = ROOT / "scripts" / "dev" / "eval_dialogue" / "data" / "judge_results"
REPORT_DIR = ROOT / "docs" / "analysis" / "dialogue_eval"
BASELINE_DIR = ROOT / "scripts" / "dev" / "eval_dialogue" / "data" / "baseline"

PROXY_THRESHOLD = 0.5


def _rate(entries, key, subset=None):
    rs = entries if subset is None else [r for r in entries if r["channel"] == subset]
    vals = [r["metrics"][key] for r in rs if r["metrics"].get(key) is not None and isinstance(r["metrics"][key], (int, float))]
    return (sum(vals) / len(vals), len(vals), len(rs)) if vals else (None, 0, len(rs))


def _classify_failure(entry: dict) -> str | None:
    """失败归因分类。返回 None 表示非失败。"""
    m = entry["metrics"]
    has_gold = bool(entry.get("gold_keywords"))
    if has_gold and m.get("indoc_hit_at_5") == 0:
        if m.get("indoc_coverage", 0) == 0:
            return "作品内无块被召回（跨作品/检索跑偏）"
        return "作品内有块但未命中（数据稀疏/用字差异）"
    if m.get("speaker_hit") == 0:
        return "口吻素材缺失（角色台词少/作品范围限制）"
    return None


def _fmt(v) -> str:
    return "—" if v is None else f"{v:.1%}" if isinstance(v, float) else str(v)


def build_report(result_path: Path, judge_path: Path | None) -> tuple[str, dict]:
    data = json.loads(result_path.read_text(encoding="utf-8"))
    entries = data["results"]
    meta = data["meta"]
    sha = meta.get("git_sha", "?")
    judge_rows: list[dict] = []
    if judge_path and judge_path.exists():
        judge_rows = json.loads(judge_path.read_text(encoding="utf-8"))["rows"]
    jmap = {r["case_id"]: r for r in judge_rows}

    # ── L2 代理指标汇总 ──
    lines: list[str] = []
    lines.append(f"# 对话检索质量评估报告\n")
    lines.append(f"> 时间：{meta.get('timestamp', '?')} | git: `{sha}` | 评估集 v{meta.get('seed_version')} | {meta.get('case_count')} case")
    lines.append(f"> 环境：embedding=`{meta.get('embedding')}` · reranker=`{meta.get('reranker')}` · top_k=`{meta.get('top_k')}` · 耗时 {meta.get('elapsed_sec')}s\n")

    lines.append("## 1. 代理指标汇总（L2）\n")
    lines.append("| 指标 | 全部 | dialogue | narrative |")
    lines.append("|------|------|----------|-----------|")
    for key, label in [
        ("channel_hit_at_5", "全库通道 top5 命中"),
        ("channel_hit_at_15", "全库通道 top15 命中"),
        ("indoc_hit_at_5", "作品内 top5 命中"),
        ("indoc_coverage", "作品内块覆盖"),
        ("speaker_hit", "口吻角色台词命中"),
        ("speaker_block_coverage", "口吻块级覆盖"),
    ]:
        v, n, tot = _rate(entries, key)
        vd, nd, _ = _rate(entries, key, "dialogue")
        vn, nn, _ = _rate(entries, key, "narrative")
        lines.append(
            f"| {label} | {_fmt(v)} ({n}/{tot}) | {_fmt(vd)} ({nd}) | {_fmt(vn)} ({nn}) |"
        )

    # ── L3 judge ──
    if judge_rows:
        lines.append("\n## 2. LLM Judge 汇总（L3，DeepSeek 后端）\n")
        lines.append("| 指标 | 均分 | case 数 |")
        lines.append("|------|------|---------|")
        for key, label in [("self_score", "自写相关性 judge"), ("ragas_ctx_prec", "RAGAS context_precision"), ("deepeval_ctx_prec", "DeepEval contextual_precision")]:
            vals = [r[key] for r in judge_rows if r[key] is not None]
            lines.append(f"| {label} | {sum(vals)/len(vals):.3f} | {len(vals)} |" if vals else f"| {label} | — | 0 |")

        lines.append("\n**一致性（同向率，阈值 0.5）**\n")
        pairs = [
            ("self_score", "proxy_indoc_hit", "自写 judge vs 代理"),
            ("ragas_ctx_prec", "self_score", "RAGAS vs 自写"),
            ("deepeval_ctx_prec", "self_score", "DeepEval vs 自写"),
            ("ragas_ctx_prec", "deepeval_ctx_prec", "RAGAS vs DeepEval"),
        ]
        for a, b, label in pairs:
            pts = [(r[a], r[b]) for r in judge_rows if r[a] is not None and r[b] is not None]
            if pts:
                agree = sum(1 for x, y in pts if (x >= PROXY_THRESHOLD) == (y >= PROXY_THRESHOLD))
                lines.append(f"- {label}：**{agree/len(pts):.1%}**（{len(pts)} case）")
            else:
                lines.append(f"- {label}：—")

    # ── 失败 case ──
    lines.append("\n## 3. 失败 case 与归因\n")
    fails = [(e, _classify_failure(e)) for e in entries]
    fails = [(e, c) for e, c in fails if c]
    lines.append(f"代理指标失败 case：**{len(fails)} / {len(entries)}**\n")
    lines.append("| case | 通道 | 归因 | query | self judge |")
    lines.append("|------|------|------|-------|-----------|")
    for e, c in fails:
        j = jmap.get(e["case_id"], {})
        self_s = j.get("self_score")
        lines.append(f"| {e['case_id']} | {e['channel']} | {c} | {e['query'][:28]} | {_fmt(self_s)} |")

    # ── 低相关性 case（self judge < 0.5）──
    low_self = [
        (e, j) for e in entries
        if (j := jmap.get(e["case_id"], {})).get("self_score") is not None and j["self_score"] < 0.5
    ]
    lines.append(f"\n**低相关性 case（自写 judge < 0.5）：{len(low_self)} / {len(entries)}**——检索命中关键词但不支撑 query 的语义差距。\n")
    lines.append("| case | 通道 | self | query | judge 理由 |")
    lines.append("|------|------|------|-------|-----------|")
    for e, j in low_self:
        lines.append(f"| {e['case_id']} | {e['channel']} | {_fmt(j['self_score'])} | {e['query'][:22]} | {(j.get('self_reason') or '')[:60]} |")

    # ── 逐 case 详情 ──
    lines.append("\n## 4. 逐 case 详情\n")
    lines.append("| case | 通道 | intent | 代理top5 | 自写 | RAGAS | DeepEval | query |")
    lines.append("|------|------|--------|----------|------|-------|----------|-------|")
    for e in entries:
        m = e["metrics"]
        j = jmap.get(e["case_id"], {})
        lines.append(
            f"| {e['case_id']} | {e['channel']} | {e['intent']} | {_fmt(m.get('indoc_hit_at_5'))} | "
            f"{_fmt(j.get('self_score'))} | {_fmt(j.get('ragas_ctx_prec'))} | {_fmt(j.get('deepeval_ctx_prec'))} | {e['query'][:22]} |"
        )

    # ── 发现的问题 ──
    lines.append("\n## 5. 本次评估发现的生产问题\n")
    routed = {}
    for e in entries:
        ch = e["metrics"].get("routed_channel")
        routed[ch] = routed.get(ch, 0) + 1
    lines.append(f"- **IntentRouter 路由分布**：{dict(sorted(routed.items()))}——口吻模仿 query 生产上多数路由到 `qa` 通道（65/89），`dialogue` 通道检索由 style 检索独立承担（不走 router）。")
    lines.append("- **lance_filters characters 预过滤 AND bug**：多别名 `filters={'characters': [...]}` 用 AND 连接（要求块同时含所有别名）→ 几乎必然返回空 → 生产 style 检索实际依赖 fallback 无过滤全库检索。")
    if judge_rows:
        low = [r for r in judge_rows if r["self_score"] is not None and r["self_score"] < 0.5]
        lines.append(f"- **低相关性 case {len(low)} 条**（自写 judge < 0.5）：多集中在寒暄/指代类 query（无实体 gold）与数据稀疏角色。")

    md = "\n".join(lines) + "\n"

    # JSON 版
    payload = {
        "meta": meta,
        "proxy_summary": {
            k: _rate(entries, k)[0] for k in
            ["channel_hit_at_5", "channel_hit_at_15", "indoc_hit_at_5", "indoc_coverage", "speaker_hit", "speaker_block_coverage"]
        },
        "proxy_summary_dialogue": {k: _rate(entries, k, "dialogue")[0] for k in
            ["channel_hit_at_5", "channel_hit_at_15", "indoc_hit_at_5", "indoc_coverage", "speaker_hit", "speaker_block_coverage"]},
        "proxy_summary_narrative": {k: _rate(entries, k, "narrative")[0] for k in
            ["channel_hit_at_5", "channel_hit_at_15", "indoc_hit_at_5", "indoc_coverage", "speaker_hit", "speaker_block_coverage"]},
        "failures": [{"case_id": e["case_id"], "reason": c, "query": e["query"]} for e, c in fails],
        "judge": {"rows": judge_rows},
    }
    return md, payload


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", type=str, default="", help="L2 结果文件（默认最新）")
    ap.add_argument("--judge", type=str, default="", help="L3 judge 文件（默认最新，可无）")
    args = ap.parse_args()

    result_path = Path(args.results) if args.results else sorted(RESULT_DIR.glob("*_results*.json"))[-1]
    judge_path = Path(args.judge) if args.judge else (sorted(JUDGE_DIR.glob("*_judge.json"))[-1] if list(JUDGE_DIR.glob("*_judge.json")) else None)

    md, payload = build_report(result_path, judge_path)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)

    sha = payload["meta"].get("git_sha", "?")
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    md_path = REPORT_DIR / f"{date}_{sha}.md"
    md_path.write_text(md, encoding="utf-8")
    json_path = REPORT_DIR / f"{date}_{sha}.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    # baseline diff
    latest = BASELINE_DIR / "dialogue_eval_latest.json"
    if latest.exists():
        prev = json.loads(latest.read_text(encoding="utf-8"))
        print("=== 与上次基线 diff（跌超 5pp 标红）===")
        for k, v in payload["proxy_summary"].items():
            pv = prev.get("proxy_summary", {}).get(k)
            if v is not None and pv is not None:
                d = v - pv
                flag = " ⚠️" if d < -0.05 else ""
                print(f"  {k:<22} {pv:.2%} → {v:.2%} ({d:+.2%}){flag}")
    latest.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\n报告：{md_path}")
    print(f"JSON：{json_path}")
    print(f"基线：{latest}")


if __name__ == "__main__":
    main()
