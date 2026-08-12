# -*- coding: utf-8 -*-
"""接入点验证：走 blocks.build_inventory 完整链路（LLM backend）。

验证：llm_client 传入 → LLM 盘点生效 → persist_inventory_candidates
      → seed_names_from_inventory → 返回（真实 ingest 接入点路径）。
"""
import asyncio
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

_env = ROOT / ".env"
if _env.exists():
    load_dotenv(_env, encoding="utf-8-sig")

SRC = Path(r"C:\Users\10650\Desktop\毕设\知识库\史莱姆\小说\txt\txt卷 至21\txt卷.txt")
MAX_CHARS = 120_000


class _Ch:
    def __init__(self, title: str, text: str):
        self.title = title
        self.text = text


class _Doc:
    def __init__(self, chapters: list[_Ch]):
        self.chapters = chapters


def load_text() -> list[_Ch]:
    raw = open(SRC, "rb").read()
    text = raw.decode("gb18030", errors="replace")
    parts = re.split(r"\n\s*(第[一二三四五六七八九十百0-9]+卷[^\n]*)\s*\n", text)
    chapters: list[_Ch] = []
    title = "开篇"
    buf: list[str] = []
    used = 0
    for i, seg in enumerate(parts):
        if i % 2 == 1:
            title = seg.strip()
        else:
            if seg.strip():
                buf.append(seg)
            if i % 2 == 0 and buf:
                joined = "\n".join(buf)
                chapters.append(_Ch(title, joined))
                used += len(joined)
                buf = []
            if used >= MAX_CHARS:
                break
    if buf:
        chapters.append(_Ch(title, "\n".join(buf)))
    return chapters


async def main() -> None:
    from src.application.novel.ingest.blocks import build_inventory

    chapters = load_text()
    doc = _Doc(chapters)
    print(f"样本: {len(chapters)} 卷/段, {sum(len(c.text) for c in chapters)} 字符")

    t0 = time.time()
    inventory_result, seed_names = await build_inventory(
        doc, series_id="史莱姆验证", doc_id="史莱姆验证__vol01"
    )
    elapsed = time.time() - t0

    print(f"\n耗时: {elapsed:.1f}s（LLM 盘点 + 归一 + persist）")
    if inventory_result is None:
        print("ERROR: inventory_result 为 None（可能被异常跳过）")
        return
    print(f"meta: {inventory_result.meta}")
    print(f"角色数: {len(inventory_result.characters)}")
    for c in sorted(inventory_result.characters, key=lambda x: -x.mention_count)[:15]:
        print(f"  {c.canonical_name:12} m={c.mention_count:4} aliases={c.aliases} imp={c.importance}")
    print(f"\nseed_names（对话归因 volume_seed）: {seed_names}")
    print(f"inventory 落盘: data/inventories/史莱姆验证.json")


if __name__ == "__main__":
    asyncio.run(main())
