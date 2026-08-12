# -*- coding: utf-8 -*-
"""打印归一输入：关键簇的 surfaces + evidence（验证上下文充分性）"""
import asyncio, sys
from pathlib import Path

ROOT = Path(r"D:\tools\agent")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "venv" / "Lib" / "site-packages"))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env", encoding="utf-8-sig")


async def main():
    from src.application.novel.redialogue import rebuild_chapters
    from src.application.novel.ingest.convert import _build_shared_llm
    from src.domain.novel.character_inventory.llm_ner import (
        extract_names_by_chapter_batches, mentions_from_names,
    )
    from src.domain.novel.character_ner import cluster_mentions
    from src.domain.novel.character_inventory import inventory_config
    from types import SimpleNamespace

    doc_id = "关于我转生变成史莱姆这档事__vol01"
    series_id = "关于我转生变成史莱姆这档事"
    chapters = rebuild_chapters(doc_id)
    text = "\n\n".join(f"【{c.title}】\n{c.text}" for c in chapters)
    print(f"全文: {len(text):,} 字符, {len(chapters)} 章")

    cfg = inventory_config()
    llm = _build_shared_llm(temperature=0.0, max_tokens=4096, timeout=90.0, endpoint="character_inventory")
    try:
        names = await extract_names_by_chapter_batches(
            chapters, llm,
            batch_chars=int(cfg.get("llm_batch_chars", 60000)),
            max_names=int(cfg.get("llm_max_names", 60)),
            max_tokens=int(cfg.get("llm_max_tokens", 2048)),
        )
    finally:
        if llm:
            try: await llm.close()
            except Exception: pass
    print(f"粗召回 names: {len(names)} 个")

    mentions = mentions_from_names(text, names)
    clusters = cluster_mentions(mentions, min_mentions=2, text=text)
    print(f"clusters: {len(clusters)} 个")

    # 打印目标噪声簇的 surfaces + evidence
    targets = ["矮人", "勇者", "魔王", "焰之巨人", "大贤者", "捕食者", "黑骑士", "克莉丝蒂", "海伦"]
    for c in clusters:
        sur = "".join(c.surfaces[:3])
        if any(t in sur for t in targets):
            print(f"\n=== 簇 {c.cluster_id} | surfaces={c.surfaces[:5]} | count={c.count} ===")
            for ev in c.evidence[:4]:
                print(f"  证据: …{ev.strip()[:120]}…")


if __name__ == "__main__":
    asyncio.run(main())
