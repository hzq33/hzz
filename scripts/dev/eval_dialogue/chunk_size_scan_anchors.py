"""chunk_size_scan_anchors.py — 分块大小扫描 v3：答案句定位评估。

用测试书 e2e_story_book.md 原文，均匀抽取 N 个事实句（20-90 字、含专名/事件
词），query = 句子本身，gold = 句子开头所在块。评估"向量检索能否定位到
包含该句的块"——这是真正的定位精度测试（实体命中版已饱和，无区分度）。

指标：
- anchor_hit@1 / @5 / @15：gold 块进 top-k 的比例
- top1_precision：命中 top-1 时，gold 块文本长度 / top-1 块文本长度
  （衡量"定位精准度"：小块命中即精准，大块命中但块内噪声多）

用法：PYTHONPATH="./venv/Lib/site-packages" ./venv/Scripts/python.exe scripts/dev/eval_dialogue/chunk_size_scan_anchors.py
"""

from __future__ import annotations

import asyncio
import json
import random
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
SCAN_SIZES = [80, 100, 150, 200, 300, 400, 600, 800]
N_ANCHORS = 40
SEED = 42

# 事件/专名词（anchor 选取时保证句子有信息量）
NAME_RE = re.compile(r"(亚瑟|莉娜|维克托|艾琳|雷恩|卡洛琳|玛拉|老首领|马库斯|铁山|小虎|周诚|黑旗军|黑鸦堡|灰脊|哨站|永冻之心|雪莲|山口|霍恩|柱子|阿贵|小卡|瘟疫|流寇|暴风雪|战役|战争|死|杀|背叛|投降|救|喜欢|爱|恨)")


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[。！？!?])", text)
    return [p.strip() for p in parts if p.strip()]


def _load_chapters() -> list[tuple[str, str]]:
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
    return chapters


def _pick_anchors(chapters: list[tuple[str, str]], n: int) -> list[str]:
    """均匀抽取含专名/事件词、长度 20-90 的句子作为锚点。"""
    cands: list[tuple[str, int]] = []  # (sentence, chapter_idx)
    for ci, (_, body) in enumerate(chapters):
        for s in _split_sentences(body):
            s = s.replace("\n", "")
            if 20 <= len(s) <= 90 and NAME_RE.search(s):
                cands.append((s, ci))
    rng = random.Random(SEED)
    # 按章分层抽样，避免集中在前几章
    by_ch: dict[int, list[str]] = {}
    for s, ci in cands:
        by_ch.setdefault(ci, []).append(s)
    picked: list[str] = []
    keys = sorted(by_ch)
    idx = 0
    while len(picked) < n and keys:
        k = keys[idx % len(keys)]
        if by_ch[k]:
            picked.append(by_ch[k].pop(rng.randrange(len(by_ch[k]))))
        else:
            keys.remove(k)
            idx -= 1
        idx += 1
    return picked


def _chunk_all(chapters: list[tuple[str, str]], child_chars: int):
    """与真实入库一致：按章 clean → HierarchicalChunker。返回 (texts, 每块的句子锚点映射)。"""
    from src.domain.novel.chunker import MDCleaner, HierarchicalChunker

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
    block_heads: list[str] = []  # 每块前 15 字（锚点归属用）
    for ci, (title, body) in enumerate(chapters):
        cleaned = cleaner.clean(body, doc_id="e2e_story")
        cleaned.chapter_title = title
        blocks = chunker.chunk(cleaned, doc_id="e2e_story", chapter_index=ci)
        for b in blocks:
            if getattr(b, "granularity", "") == "child":
                t = b.narrative_text
                texts.append(t)
                block_heads.append(t[:15])
    return texts, block_heads


def _gold_index(anchor: str, block_heads: list[str], texts: list[str]) -> int:
    """包含 anchor 开头 10 字的块 = gold 块（小块下句子可能跨块，取开头所在块）。"""
    head = anchor[:10]
    best = -1
    best_pos = 10**9
    for i, t in enumerate(texts):
        pos = t.find(head)
        if pos >= 0 and pos < best_pos:
            best_pos = pos
            best = i
    if best >= 0:
        return best
    # 兜底：anchor 任一前 6 字片段
    for i, t in enumerate(texts):
        for k in range(10, 5, -1):
            if t.find(anchor[:k]) >= 0:
                return i
    return -1


async def main() -> None:
    import numpy as np

    from src.infrastructure.embedding import Qwen3EmbeddingProvider

    chapters = _load_chapters()
    anchors = _pick_anchors(chapters, N_ANCHORS)
    print(f"章节 {len(chapters)}，锚点句 {len(anchors)} 个", flush=True)
    print("锚点示例:", anchors[:2], flush=True)

    embedder = Qwen3EmbeddingProvider(model_path="models/Qwen3-Embedding-0.6B", device="auto", use_fp16=True)
    qres = await embedder.embed_texts(anchors)
    qvecs = np.array(qres.embeddings)
    qn = np.linalg.norm(qvecs, axis=1, keepdims=True)
    qvecs = qvecs / np.clip(qn, 1e-9, None)
    print("锚点向量完成", flush=True)

    async def run_size(child_chars: int) -> dict:
        texts, heads = _chunk_all(chapters, child_chars)
        avg = sum(len(t) for t in texts) // max(1, len(texts))
        print(f"  child={child_chars}: {len(texts)} 块, 平均 {avg} 字", flush=True)

        vecs = np.array((await embedder.embed_texts(texts)).embeddings)
        vn = np.linalg.norm(vecs, axis=1, keepdims=True)
        vecs = vecs / np.clip(vn, 1e-9, None)

        hit1 = hit5 = hit15 = 0
        prec1_sum = 0.0
        for a, qv in zip(anchors, qvecs):
            gold = _gold_index(a, heads, texts)
            if gold < 0:
                continue
            scores = vecs @ qv
            top = np.argsort(-scores)[:15].tolist()
            hit1 += int(top[0] == gold)
            hit5 += int(gold in top[:5])
            hit15 += int(gold in top)
            if top[0] == gold:
                prec1_sum += len(texts[gold]) / max(1, len(texts[top[0]]))
        n = N_ANCHORS
        return {
            "child_chars": child_chars,
            "n_child": len(texts),
            "avg_chars": avg,
            "hit1": hit1, "hit5": hit5, "hit15": hit15, "n": n,
            "hit1_rate": round(hit1 / n, 4),
            "hit5_rate": round(hit5 / n, 4),
            "hit15_rate": round(hit15 / n, 4),
            "top1_prec": round(prec1_sum / max(1, hit1), 4),
        }

    results = []
    for size in SCAN_SIZES:
        r = await run_size(size)
        results.append(r)
        print(
            f"  child={size:>4}: 定位@1 {r['hit1_rate']:.1%} ({r['hit1']}/{r['n']})  "
            f"@5 {r['hit5_rate']:.1%} ({r['hit5']}/{r['n']})  @15 {r['hit15_rate']:.1%}  "
            f"top1精准度 {r['top1_prec']:.2f}",
            flush=True,
        )

    print("\n=== 分块大小扫描汇总（答案句定位）===")
    print(f"{'child':>6} {'块数':>6} {'均字':>5} {'定位@1':>8} {'定位@5':>8} {'定位@15':>8} {'top1精准':>8}")
    for r in results:
        print(
            f"{r['child_chars']:>6} {r['n_child']:>6} {r['avg_chars']:>5} "
            f"{r['hit1_rate']:>7.1%} {r['hit5_rate']:>7.1%} {r['hit15_rate']:>7.1%} {r['top1_prec']:>8.3f}"
        )

    out = ROOT / "scripts" / "dev" / "eval_dialogue" / "data" / "results" / "chunk_size_scan_anchors.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n结果: {out}")


if __name__ == "__main__":
    asyncio.run(main())
