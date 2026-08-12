# -*- coding: utf-8 -*-
"""史莱姆 vol01 glm 串行分批盘点验证"""
import asyncio, sys, time
from pathlib import Path

ROOT = Path(r"D:\tools\agent")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "venv" / "Lib" / "site-packages"))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env", encoding="utf-8-sig")


async def main():
    from src.application.novel.redialogue import rebuild_chapters
    from src.application.novel.ingest.convert import _build_shared_llm
    from src.domain.novel.character_inventory import (
        build_character_inventory, inventory_config,
        persist_inventory_candidates, seed_names_from_inventory,
    )
    from types import SimpleNamespace

    doc_id = "关于我转生变成史莱姆这档事__vol01"
    series_id = "关于我转生变成史莱姆这档事"
    chapters = rebuild_chapters(doc_id)
    total = sum(len(c.text) for c in chapters)
    print(f"文档: {doc_id}, {len(chapters)} 章, {total:,} 字符")

    llm = _build_shared_llm(
        temperature=0.0, max_tokens=4096, timeout=240.0, endpoint="character_inventory",
    )
    normalize_llm = _build_shared_llm(
        temperature=0.0, max_tokens=4096, timeout=240.0,
        endpoint="character_inventory_normalize",
    )
    print(f"LLM(提取): {llm}")
    print(f"LLM(归一): {normalize_llm}")
    cfg = inventory_config()
    print(f"batch_chars: {cfg.get('llm_batch_chars')}")

    t0 = time.time()
    try:
        result = await build_character_inventory(
            SimpleNamespace(chapters=chapters),
            series_id=series_id,
            llm_client=llm,
            normalize_llm_client=normalize_llm,
            config=cfg,
        )
    finally:
        if llm is not None:
            try:
                await llm.close()
            except Exception:
                pass
        if normalize_llm is not None:
            try:
                await normalize_llm.close()
            except Exception:
                pass

    print(f"\n===== 盘点完成 ({time.time()-t0:.0f}s) =====")
    print(f"角色数: {len(result.characters)} | dropped: {len(result.dropped)}")
    print(f"llm_calls: {result.llm_calls} | llm_skipped: {result.llm_skipped}")
    print(f"meta: {result.meta}")

    print("\n角色名单 (按 mention_count):")
    for c in sorted(result.characters, key=lambda x: -x.mention_count):
        print(f"  {c.canonical_name:16s} mention={c.mention_count:4d} imp={c.importance} aliases={c.aliases[:3]}")
    print("\ndropped:")
    for d in result.dropped:
        print("  ", d)

    if result.characters:
        persist_inventory_candidates(series_id=series_id, doc_id=doc_id, inventory=result)
        print(f"\n已落盘 data/inventories/{series_id}.json")

    if result.relations:
        from src.domain.novel.character_inventory import persist_relations
        p = persist_relations(series_id, result.relations)
        print(f"已落盘关系数据: {p}")
        print(f"\n关系数: {len(result.relations)}")
        for r in result.relations[:15]:
            ev = r.get('evidence', [])[:1]
            print(f"  {r.get('source',''):10s} → {r.get('target',''):10s} [{r.get('relation','')}] ch{r.get('first_chapter')}×{r.get('chapter_count')} ev={ev}")


if __name__ == "__main__":
    asyncio.run(main())
