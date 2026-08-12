# -*- coding: utf-8 -*-
"""史莱姆 vol01 入库（ingest）— 验证 Qwen3-8B 全卷盘点"""
import asyncio, sys, time
from pathlib import Path

ROOT = Path(r"D:\tools\agent")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "venv" / "Lib" / "site-packages"))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env", encoding="utf-8-sig")


async def main():
    from src.application.novel.ingest import ingest_novel

    src = r"C:\Users\10650\Desktop\毕设\知识库\史莱姆\小说\epub\关于我转生变成史莱姆这档事 - 01.epub"
    data = Path(src).read_bytes()
    print(f"文件: {src} ({len(data)/1e6:.1f} MB)")

    t0 = time.time()

    def on_progress(stage, msg, pct):
        print(f"  [{pct:3d}%] {stage}: {msg}", flush=True)

    result = await ingest_novel(
        data,
        "关于我转生变成史莱姆这档事 - 01.epub",
        series_id="关于我转生变成史莱姆这档事",
        volume_no=1,
        generate_qa=False,
        generate_character_llm=False,
        on_progress=on_progress,
    )
    print(f"\n===== ingest 完成 ({time.time()-t0:.0f}s) =====")
    print("doc_id:", result.doc_id)
    print("source_format:", result.source_format)
    print("error:", result.error)
    print("chapters:", result.chapters)
    print("narrative_blocks:", result.narrative_blocks)
    print("dialogue_blocks:", result.dialogue_blocks)
    print("character_blocks:", result.character_blocks)
    print("qa_blocks:", result.qa_blocks)
    print("characters:", result.characters)
    print("skipped:", result.skipped)

    # inventory 结果（Qwen3-8B 盘点）
    inv = getattr(result, "inventory_result", None)
    if inv is not None:
        print("\n===== Qwen3-8B 盘点结果 =====")
        print("角色数:", len(inv.characters))
        print("dropped:", len(inv.dropped))
        print("llm_calls:", inv.llm_calls)
        print("llm_skipped:", inv.llm_skipped)
        print("meta:", inv.meta)
        print("\n角色名单 (按 mention_count):")
        for c in sorted(inv.characters, key=lambda x: -x.mention_count):
            print(f"  {c.canonical_name:16s} mention={c.mention_count:4d} imp={c.importance} aliases={c.aliases[:4]}")


if __name__ == "__main__":
    asyncio.run(main())
