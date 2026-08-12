"""触发 story_analysis 生成语义关系数据（角色关系图谱数据源）。

复用 run_story_analysis（map/reduce），产出 RelationChange 落盘到
data/story_analyses/{series_id}.json，并写入 LanceDB character 通道。

用法：
    PYTHONPATH="./venv/Lib/site-packages;." ./venv/Scripts/python.exe scripts/dev/build_story_analysis.py
    # 可选参数：--series 败犬女主太多了 --max-chapters 40
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

_env = ROOT / ".env"
if _env.exists():
    load_dotenv(_env)


def _build_llm():
    """从 config.yaml 构建 DeepSeek SharedLLMClient（thinking 关闭，保纯 JSON 输出）。"""
    import yaml
    from src.shared.llm import SharedLLMClient

    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    agent_cfg = cfg.get("agent", {})
    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY 为空，无法跑 story_analysis")
    base_url = agent_cfg.get("base_url", "https://api.deepseek.com")
    model = agent_cfg.get("model", "deepseek-v4-flash")
    fallback_model = agent_cfg.get("fallback_model", "")
    fallback = None
    if fallback_model:
        fallback = {"base_url": base_url, "api_key": api_key, "model": fallback_model}
    from src.domain.novel.story_analysis import story_analysis_max_tokens

    return SharedLLMClient(
        primary={"base_url": base_url, "api_key": api_key, "model": model},
        fallback=fallback,
        temperature=0.2,
        max_tokens=story_analysis_max_tokens(),
        thinking_disabled=True,
    )


async def _progress(p: dict) -> None:
    phase = p.get("phase", "")
    msg = p.get("message", "")
    extra = ""
    if p.get("chapter_title"):
        extra = f" | {p.get('doc_id', '')}#{p.get('chapter_title')}"
    print(f"  [{phase}] {msg}{extra}", flush=True)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--series", default="败犬女主太多了")
    parser.add_argument("--max-chapters", type=int, default=None)
    parser.add_argument("--doc-id", default=None, help="只跑单卷（如 败犬女主太多了__vol01）")
    parser.add_argument("--no-force", action="store_true", help="不强制刷新（用缓存）")
    args = parser.parse_args()

    from src.application.novel.factory import create_novel_store
    from src.domain.novel.story_analysis import run_story_analysis

    print(f"装配 store + LLM …", flush=True)
    store = create_novel_store()
    llm = _build_llm()
    print(f"  series: {args.series}", flush=True)
    print(f"  doc_id: {args.doc_id or '(全系列)'}", flush=True)
    print(f"  max_chapters: {args.max_chapters or '(config 默认 40)'}", flush=True)

    print(f"\n开始 story_analysis …", flush=True)
    snap = await run_story_analysis(
        series_id=args.series,
        store=store,
        llm_client=llm,
        doc_id=args.doc_id,
        force=not args.no_force,
        max_chapters=args.max_chapters,
        on_progress=_progress,
    )

    rels = snap.relations or []
    pol_dist: dict[str, int] = {}
    type_dist: dict[str, int] = {}
    pairs: set[tuple[str, str]] = set()
    for r in rels:
        pol_dist[r.polarity] = pol_dist.get(r.polarity, 0) + 1
        type_dist[r.relation_type or "(空)"] = type_dist.get(r.relation_type or "(空)", 0) + 1
        pairs.add(tuple(sorted([r.source, r.target])))

    print(f"\n=== 完成 ===", flush=True)
    print(f"  relations: {len(rels)} 条 / {len(pairs)} 个角色对", flush=True)
    print(f"  events:    {len(snap.events or [])} 条", flush=True)
    print(f"  polarity 分布: {dict(sorted(pol_dist.items(), key=lambda x: -x[1]))}", flush=True)
    print(f"  relation_type Top10: {dict(sorted(type_dist.items(), key=lambda x: -x[1])[:10])}", flush=True)
    print(f"  stats: {snap.stats}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
