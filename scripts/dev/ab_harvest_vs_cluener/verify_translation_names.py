# -*- coding: utf-8 -*-
"""翻译名场景验证：史莱姆（外来语/日文汉字名多）LLM backend vs CLUENER。

背景：CLUENER 在史莱姆上仅召回 2-4 个候选（新闻语料 NER 不认识翻译名）。
验证 LLM 全量盘点在该场景显著更优。

用法：
    PYTHONIOENCODING=utf-8 ./venv/Scripts/python.exe scripts/dev/ab_harvest_vs_cluener/verify_translation_names.py
"""

from __future__ import annotations

import asyncio
import json
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

# 第一卷 gold（对齐文本实际译名：利姆路/维尔德拉/卡巴尔/爱莲）
GOLD = [
    ("利姆露", ["利姆路", "利姆鲁", "利姆露"]),
    ("维鲁多拉", ["维尔德拉", "维鲁德拉"]),
    ("兰加", ["岚牙"]),
    ("红丸", []),
    ("苍影", []),
    ("紫苑", []),
    ("安奴", []),
    ("戈布达", ["哥布达"]),
    ("戈布蒂", ["哥布蒂"]),
    ("静", ["静江", "井泽静江", "希兹"]),
    ("幸平", []),
    ("卡巴鲁", ["卡巴尔"]),
    ("艾伦", ["爱莲"]),
    ("凯萨琳", []),
]


def norm(s: str) -> str:
    return re.sub(r"[\s·•、，。！？「」『』“”\"',.:;!?\-—（）()]", "", s or "")


def match_gold(candidate: str) -> str | None:
    c = norm(candidate)
    if len(c) < 2:
        return None
    for canon, aliases in GOLD:
        for ref in [canon, *aliases]:
            if norm(ref) == c:
                return canon
    best: str | None = None
    best_len = -1
    for canon, aliases in GOLD:
        for ref in [canon, *aliases]:
            r = norm(ref)
            if len(r) >= 2 and (c in r or r in c):
                if len(r) > best_len:
                    best, best_len = canon, len(r)
    return best


class _Ch:
    def __init__(self, title: str, text: str):
        self.title = title
        self.text = text


class _Doc:
    def __init__(self, chapters: list[_Ch]):
        self.chapters = chapters


def load_text() -> tuple[list[_Ch], int]:
    """读史莱姆 txt（gb18030），按卷/章切分，取前 MAX_CHARS。"""
    raw = open(SRC, "rb").read()
    text = raw.decode("gb18030", errors="replace")
    # 按 "第X卷" 切分（台版结构：卷 > 章）
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
    return chapters, used


async def main() -> None:
    from src.shared.llm_factory import create_shared_llm
    from src.utils.config import load_config
    from src.domain.novel.character_inventory.builder import build_character_inventory

    chapters, used = load_text()
    print(f"源文件: {SRC.name}（gb18030 解码）")
    print(f"样本: {len(chapters)} 卷/段, {used} 字符")
    doc = _Doc(chapters)

    config = load_config(str(ROOT / "config.yaml"))
    llm = create_shared_llm(config, temperature=0.0, max_tokens=2048)

    t0 = time.time()
    result = await build_character_inventory(doc, series_id="", llm_client=llm)
    elapsed = time.time() - t0

    print(f"\n{'='*64}\n史莱姆 LLM backend inventory 产出（{elapsed:.1f}s）\n{'='*64}")
    chars = result.characters or []
    print(f"角色数: {len(chars)}")
    for c in sorted(chars, key=lambda x: -x.mention_count):
        print(f"  {c.canonical_name:10} m={c.mention_count:4} aliases={c.aliases} imp={c.importance}")

    all_names = [n for c in chars for n in [c.canonical_name, *(c.aliases or [])]]
    hits = {match_gold(n) for n in all_names if match_gold(n)}
    print(f"\ngold 召回: {len(hits)}/{len(GOLD)} ({len(hits)/len(GOLD)*100:.1f}%)")
    print(f"  [CLUENER 旧数据: 史莱姆 inventory 仅 2-4 个候选]")
    # 区分"样本外未出场"与"样本内漏提取"
    sample_text = "\n".join(ch.text for ch in chapters)
    missed = [g for g, _ in GOLD if g not in hits]
    out_of_sample = [
        g for g in missed
        if all(norm(a) not in norm(sample_text) for a in
               [g, *dict(GOLD)[g]]) and g not in norm(sample_text)
    ]
    in_sample_missed = [g for g in missed if g not in out_of_sample]
    print(f"未命中 gold: {missed}")
    print(f"  ├ 样本外(未出场, 不计失败): {out_of_sample}")
    print(f"  └ 样本内但漏提取: {in_sample_missed if in_sample_missed else '无 — 样本内角色 100% 覆盖'}")
    print(f"全部角色({len(chars)}): {[c.canonical_name for c in chars]}")


if __name__ == "__main__":
    asyncio.run(main())
