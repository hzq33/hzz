# -*- coding: utf-8 -*-
"""真实 LLM 端到端验证：llm backend 的 build_character_inventory 产出。

对比 A/B（CLUENER 72.2% 召回 + 4 噪声 + 截断名）：
  预期：零噪声、长名完整、召回与 CLUENER 持平。

用法：
    PYTHONIOENCODING=utf-8 ./venv/Scripts/python.exe scripts/dev/ab_harvest_vs_cluener/verify_llm_backend.py
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

DOC_PREFIX = "败犬女主太多了__vol01"
MAX_CHAPTERS = 8

# 与 A/B 脚本相同的 gold（18 角色）与噪声判定
GOLD = [
    ("八奈见杏菜", ["八奈见", "八奈", "杏菜"]), ("烧盐柠檬", ["烧盐", "柠檬"]),
    ("白玉莉子", ["白玉", "莉子"]), ("温水和彦", ["温水", "温水君"]),
    ("温水佳树", ["佳树"]), ("小鞠知花", ["小鞠"]),
    ("志喜屋梦子", ["志喜屋", "志喜屋学姐"]), ("马剃天爱星", ["马剃", "天爱星"]),
    ("甘夏古奈美", ["甘夏", "甘夏老师"]), ("月之木古都", ["月之木", "月之木学姐"]),
    ("绫野光希", ["绫野", "光希"]), ("朝云千早", ["朝云", "千早"]),
    ("玉木慎太郎", ["玉木", "慎太郎"]), ("樱井弘人", ["樱井", "弘人"]),
    ("姬宫华恋", ["姬宫", "华恋"]), ("权藤亚咲美", ["权藤", "亚咲美"]),
    ("袴田草介", ["草介"]), ("橘聪", ["橘"]),
]
_WRITER_FRAGS = {"太宰", "三岛", "川端", "芥川", "夏目", "村上", "太宰治", "三岛由纪夫"}


def norm(s: str) -> str:
    return re.sub(r"[\s·•、，。！？「」『』“”\"',.:;!?\-—]", "", s or "")


def match_gold(candidate: str) -> str | None:
    """候选名命中 gold 返回 canonical，否则 None。

    精确匹配优先（避免共享前缀歧义）；子串匹配取最长 ref。
    """
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


def load_chapters() -> list[tuple[str, str]]:
    from src.infrastructure.lance_backend import LanceDBBackend

    backend = LanceDBBackend(db_path="./data/novel_lance")
    arrow = backend._table.to_arrow()
    mask = [str(d).startswith(DOC_PREFIX) for d in arrow.column("doc_id").to_pylist()]
    sub = arrow.filter(mask)
    titles = sub.column("chapter_title").to_pylist()
    texts = sub.column("narrative_text").to_pylist()
    by_chapter: dict[str, list[str]] = {}
    for t, txt in zip(titles, texts):
        t = str(t or "未命名")
        txt = str(txt or "").strip()
        if txt:
            by_chapter.setdefault(t, []).append(txt)
    chapters = [(t, "\n".join(v)) for t, v in by_chapter.items()]

    def ch_no(item):
        m = re.search(r"(\d+)", item[0])
        return int(m.group(1)) if m else 9999

    chapters.sort(key=ch_no)
    return chapters[:MAX_CHAPTERS]


class _Ch:
    def __init__(self, title: str, text: str):
        self.title = title
        self.text = text


class _Doc:
    def __init__(self, chapters: list[_Ch]):
        self.chapters = chapters


async def main() -> None:
    from src.shared.llm_factory import create_shared_llm
    from src.utils.config import load_config
    from src.domain.novel.character_inventory.builder import build_character_inventory

    chapters = load_chapters()
    total_chars = sum(len(t) for _, t in chapters)
    print(f"样本: {len(chapters)} 章, {total_chars} 字符")
    doc = _Doc([_Ch(t, text) for t, text in chapters])

    config = load_config(str(ROOT / "config.yaml"))
    llm = create_shared_llm(config, temperature=0.0, max_tokens=2048)

    t0 = time.time()
    result = await build_character_inventory(doc, series_id="", llm_client=llm)
    elapsed = time.time() - t0

    print(f"\n{'='*64}\nLLM backend inventory 产出（{elapsed:.1f}s）\n{'='*64}")
    print("meta:", json.dumps(
        {k: v for k, v in (result.meta or {}).items() if k in
         ("mentions", "clusters", "kept", "dropped", "llm_calls", "cluster_fallback", "text_chars")},
        ensure_ascii=False,
    ))
    chars = result.characters or []
    print(f"\n角色数: {len(chars)}")
    for c in sorted(chars, key=lambda x: -x.mention_count):
        print(f"  {c.canonical_name:8} m={c.mention_count:4} aliases={c.aliases} imp={c.importance}")

    # 质量评估
    all_names = [n for c in chars for n in [c.canonical_name, *(c.aliases or [])]]
    hits = {match_gold(n) for n in all_names if match_gold(n)}
    noise = [n for n in all_names if norm(n) in _WRITER_FRAGS or len(norm(n)) < 2]
    print(f"\ngold 召回: {len(hits)}/{len(GOLD)} ({len(hits)/len(GOLD)*100:.1f}%)  [CLUENER A/B: 72.2%]")
    print(f"噪声: {noise if noise else '0'}  [CLUENER A/B: 4 作家名]")
    truncated = [c.canonical_name for c in chars if c.canonical_name.endswith(("学", "奈", "古")) and len(c.canonical_name) == 4]
    print(f"疑似截断名: {truncated if truncated else '无'}  [CLUENER A/B: 月之木学/甘夏古奈]")
    missed = [g for g, _ in GOLD if g not in hits]
    print(f"未命中 gold: {missed}")


if __name__ == "__main__":
    asyncio.run(main())
