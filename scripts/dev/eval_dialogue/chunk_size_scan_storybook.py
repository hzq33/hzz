"""chunk_size_scan_storybook.py — 分块大小扫描（测试书 e2e_story_book.md 版）。

与 chunk_size_scan.py 的区别：
- 原文 = data/upload_tmp/e2e_story_book.md（5 万字合成书，真实入库同款）
- 分块路径 = MDCleaner.clean(章) → HierarchicalChunker（与 ingest/blocks.py 一致）
- Query = e2e_100_impersonate.py 的 100 个扮演问题（8 角色 × 四方向）
- 评估 = 实体命中（query 中出现的专名/角色名在 top-k 块文本内出现）+ 语义重叠

用法：PYTHONPATH="./venv/Lib/site-packages" ./venv/Scripts/python.exe scripts/dev/eval_dialogue/chunk_size_scan_storybook.py
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

_env = ROOT / ".env"
if _env.exists():
    load_dotenv(_env)

STORY_PATH = ROOT / "data" / "upload_tmp" / "e2e_story_book.md"
IMP_PATH = ROOT / "scripts" / "dev" / "e2e_100_impersonate.py"
SCAN_SIZES = [80, 100, 150, 200, 300, 400, 600, 800]

# 专名表：角色 + 地名 + 关键物（用于实体命中评估）
ENTITIES = [
    "亚瑟·卡恩", "亚瑟", "莉娜·沃伦", "莉娜", "维克托·黑森", "维克托",
    "艾琳·塔利斯", "艾琳", "雷恩·索恩", "雷恩", "卡洛琳·怀特", "卡洛琳",
    "玛拉·霍恩", "玛拉", "老首领", "马库斯·卡恩", "马库斯", "铁山",
    "小虎", "周诚", "黑旗军", "黑鸦堡", "灰脊哨站", "哨站", "永冻之心",
    "雪莲", "山口", "霍恩商行", "柱子", "阿贵", "小卡",
]


def _load_questions() -> list[tuple[str, str, str]]:
    """从 e2e_100_impersonate.py 提取 (角色, 方向, 问题)。"""
    src = IMP_PATH.read_text(encoding="utf-8", errors="ignore")
    # 解析 QUESTIONS dict
    m = re.search(r"QUESTIONS[^=]*=\s*(\{.*?\n\})", src, re.DOTALL)
    if not m:
        raise RuntimeError("QUESTIONS block not found")
    block = m.group(1)
    questions: list[tuple[str, str, str]] = []
    cur_char = None
    for line in block.splitlines():
        cm = re.match(r'\s*"([^"]+)":\s*\[', line)
        if cm:
            cur_char = cm.group(1)
            continue
        qm = re.match(r'\s*\("([a-z_]+)",\s*"(.+)"\),?\s*$', line.strip())
        if qm and cur_char:
            questions.append((cur_char, qm.group(1), qm.group(2)))
    return questions


def _entities_in_query(q: str) -> list[str]:
    hits = [e for e in ENTITIES if e and e in q]
    return hits


def _hit(blob: str, entities: list[str]) -> bool:
    return any(e in blob for e in entities)


def _norm_overlap(query: str, texts: list[str]) -> float:
    """query 与文本的字符重叠率（简单基线，无 embedding 依赖）。"""
    if not texts:
        return 0.0
    qchars = set(query)
    scores = []
    for t in texts:
        tchars = set(t)
        inter = len(qchars & tchars)
        scores.append(inter / max(1, len(qchars)))
    return sum(scores) / len(scores)


async def main() -> None:
    import numpy as np

    from src.domain.novel.chunker import MDCleaner, HierarchicalChunker
    from src.infrastructure.embedding import Qwen3EmbeddingProvider

    # ── 原文按章拆分（与真实入库一致）──
    raw = STORY_PATH.read_text(encoding="utf-8")
    parts = re.split(r"(?m)^#\s+", raw)
    chapters = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        lines = p.splitlines()
        title = lines[0].strip() if lines else "正文"
        body = "\n".join(lines[1:]).strip()
        if body:
            chapters.append((title, body))
    print(f"章节数: {len(chapters)}，总 {sum(len(b) for _, b in chapters)} 字", flush=True)

    # ── query 集 ──
    questions = _load_questions()
    queries = [q for _, _, q in questions]
    print(f"query 数: {len(queries)}（含实体 query: {sum(1 for q in queries if _entities_in_query(q))}）", flush=True)
    no_ent = [q for q in queries if not _entities_in_query(q)]
    if no_ent:
        print("无实体 query 示例:", no_ent[:3], flush=True)

    embedder = Qwen3EmbeddingProvider(model_path="models/Qwen3-Embedding-0.6B", device="auto", use_fp16=True)
    qres = await embedder.embed_texts(queries)
    qvecs = np.array(qres.embeddings)
    qn = np.linalg.norm(qvecs, axis=1, keepdims=True)
    qvecs = qvecs / np.clip(qn, 1e-9, None)
    print("query 向量完成", flush=True)

    def _chunk_all(child_chars: int):
        scale = child_chars / 150
        chunker = HierarchicalChunker(
            parent_chars=800,
            parent_overlap_chars=80,
            child_chars=child_chars,
            min_child_chars=int(80 * scale),
            max_child_chars=int(220 * scale),
            index_parents=False,
            chapter_prefix_in_vec=False,
        )
        cleaner = MDCleaner()
        texts: list[str] = []
        for ci, (title, body) in enumerate(chapters):
            cleaned = cleaner.clean(body, doc_id="e2e_story")
            cleaned.chapter_title = title
            blocks = chunker.chunk(cleaned, doc_id="e2e_story", chapter_index=ci)
            for b in blocks:
                if getattr(b, "granularity", "") == "child":
                    texts.append(b.narrative_text)
        return texts

    async def run_size(child_chars: int) -> dict:
        texts = _chunk_all(child_chars)
        avg = sum(len(t) for t in texts) // max(1, len(texts))
        print(f"  child={child_chars}: {len(texts)} 块, 平均 {avg} 字", flush=True)

        vecs = np.array((await embedder.embed_texts(texts)).embeddings)
        vn = np.linalg.norm(vecs, axis=1, keepdims=True)
        vecs = vecs / np.clip(vn, 1e-9, None)

        ent_hit5 = ent_hit15 = 0
        ent_q = 0
        sem5 = 0.0
        for q, qv in zip(queries, qvecs):
            ents = _entities_in_query(q)
            if not ents:
                continue
            ent_q += 1
            scores = vecs @ qv
            top = np.argsort(-scores)[:15].tolist()
            blobs5 = [texts[i] for i in top[:5]]
            blobs15 = [texts[i] for i in top]
            ent_hit5 += int(_hit(" ".join(blobs5), ents))
            ent_hit15 += int(_hit(" ".join(blobs15), ents))
            sem5 += _norm_overlap(q, blobs5)

        n = max(1, ent_q)
        return {
            "child_chars": child_chars,
            "n_child": len(texts),
            "avg_chars": avg,
            "ent_q": ent_q,
            "ent_hit5": ent_hit5, "ent_hit15": ent_hit15,
            "ent_hit5_rate": round(ent_hit5 / n, 4),
            "ent_hit15_rate": round(ent_hit15 / n, 4),
            "overlap5": round(sem5 / n, 4),
        }

    results = []
    for size in SCAN_SIZES:
        r = await run_size(size)
        results.append(r)
        print(
            f"  child={size:>4}: 实体命中@5 {r['ent_hit5_rate']:.1%} ({r['ent_hit5']}/{r['ent_q']})  "
            f"@15 {r['ent_hit15_rate']:.1%} ({r['ent_hit15']}/{r['ent_q']})  字符重叠@5 {r['overlap5']:.4f}",
            flush=True,
        )

    print("\n=== 测试书分块扫描汇总（纯向量检索，实体命中）===")
    print(f"{'child':>6} {'块数':>6} {'均字':>5} {'实体hit@5':>10} {'实体hit@15':>11} {'重叠@5':>8}")
    for r in results:
        print(
            f"{r['child_chars']:>6} {r['n_child']:>6} {r['avg_chars']:>5} "
            f"{r['ent_hit5_rate']:>9.1%} {r['ent_hit15_rate']:>10.1%} {r['overlap5']:>8.4f}"
        )

    out = ROOT / "scripts" / "dev" / "eval_dialogue" / "data" / "results" / "chunk_size_scan_storybook.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n结果: {out}")


if __name__ == "__main__":
    asyncio.run(main())
