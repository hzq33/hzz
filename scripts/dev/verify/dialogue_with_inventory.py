# -*- coding: utf-8 -*-
"""用名单做对话提取（glm 串行）并分析归因质量"""
import asyncio, sys, time
from pathlib import Path

ROOT = Path(r"D:\tools\agent")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "venv" / "Lib" / "site-packages"))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env", encoding="utf-8-sig")


async def main():
    from src.application.novel.redialogue import rebuild_chapters, load_series_inventory
    from src.application.novel.dialogue_pipeline import extract_dialogue_for_document
    from src.application.novel.ingest.convert import _build_shared_llm
    from types import SimpleNamespace

    doc_id = "关于我转生变成史莱姆这档事__vol01"
    series_id = "关于我转生变成史莱姆这档事"

    seed, chars = load_series_inventory(series_id)
    print(f"名单: seed={len(seed)}, candidates={len(chars)}")
    print(f"seed 示例: {seed[:10]}")

    chapters = rebuild_chapters(doc_id)
    total = sum(len(c.text) for c in chapters)
    print(f"文档: {len(chapters)} 章, {total:,} 字符")

    llm = _build_shared_llm(temperature=0.0, max_tokens=6144, timeout=120.0,
                            endpoint="dialogue_extract")
    print(f"LLM: {llm}")

    t0 = time.time()
    try:
        pipe = await extract_dialogue_for_document(
            SimpleNamespace(chapters=chapters),
            doc_id,
            llm_client=llm,
            volume_seed=seed or None,
            inventory_characters=chars or None,
        )
    finally:
        if llm:
            try: await llm.close()
            except Exception: pass

    m = pipe.meta
    print(f"\n===== 对话提取完成 ({time.time()-t0:.0f}s) =====")
    for k in ("provider", "mode", "llm_calls", "turns", "turns_indexed", "blocks",
              "unknown", "vocative_rejected", "unmapped_rejected", "dedupe_dropped",
              "conflicts", "stopped_reason", "harvest_calls"):
        print(f"  {k}: {m.get(k)}")

    # speaker 分布
    from collections import Counter
    spk = Counter()
    for b in pipe.blocks:
        for d in (b.dialogues or []):
            spk[str(d.speaker)] += 1
    print(f"\nspeaker 分布 (top 25):")
    for name, n in spk.most_common(25):
        print(f"  {name:14s} {n}")
    print(f"\n总说话人: {len(spk)} 个 | 未知: {spk.get('未知', 0)}")

    # 抽样 turns
    import random
    turns = []
    for b in pipe.blocks:
        ch = getattr(b, "chapter_title", "") or ""
        for d in (b.dialogues or []):
            turns.append((ch, d.speaker, d.content))
    random.seed(42)
    sample = random.sample(turns, min(15, len(turns)))
    print("\n抽样 turns:")
    for ch, s, c in sample:
        print(f"  [{ch}] {s}: {c[:30]}...")


if __name__ == "__main__":
    asyncio.run(main())
