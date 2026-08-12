"""chunk_size_scan_book.py — 分块大小扫描（通用：任意 md 书 + 答案句定位）。

用法：
  PYTHONPATH="./venv/Lib/site-packages" ./venv/Scripts/python.exe scripts/dev/eval_dialogue/chunk_size_scan_book.py \
      --md <path.md> --book <label> [--names 利姆露,维鲁多拉,...] [--anchors 40] [--sizes 80,100,150,200,300,400,600,800]

评估：从原文均匀抽 N 个事实句（含专名/事件词、长度 20-90）作 query，
gold = 句子开头所在块。纯向量检索（无 rerank），统计 定位@1/@5/@15 与
top1 精准度（命中 top1 时 gold 块长度 / top1 块长度）。
"""

from __future__ import annotations

import argparse
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

DEFAULT_SIZES = [80, 100, 150, 200, 300, 400, 600, 800]

# 通用专名/事件词（无 --names 时使用）
GENERIC_RE = re.compile(
    r"(利姆露|利姆鲁|维鲁多拉|维鲁德拉|朱菜|紫苑|红丸|苍影|白老|戈毕尔|托蕾妮|米莉姆|雷昂|"
    r"本城正幸|库洛艾|希兹|爱丽丝|勇者|魔王|开国祭|暴风龙|矮人|妖精|哥布林|战争|战役|"
    r"亚瑟|莉娜|维克托|艾琳|雷恩|卡洛琳|玛拉|老首领|马库斯|铁山|小虎|周诚|黑旗军|黑鸦堡|"
    r"灰脊|哨站|永冻之心|雪莲|山口|霍恩|柱子|阿贵|小卡|瘟疫|流寇|暴风雪|背叛|投降|"
    r"死|杀|救|喜欢|爱|恨)"
)


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[。！？!?])", text)
    return [p.strip() for p in parts if p.strip()]


def _load_chapters(md_path: Path) -> list[tuple[str, str]]:
    raw = md_path.read_text(encoding="utf-8")
    parts = re.split(r"(?m)^#\s+", raw)
    chapters = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        lines = p.splitlines()
        title = lines[0].strip() if lines else "正文"
        body = "\n".join(lines[1:]).strip()
        # 跳过"插图"等无正文章节
        if body and len(body) > 200:
            chapters.append((title, body))
    return chapters


def _pick_anchors(chapters: list[tuple[str, str]], n: int, name_re: re.Pattern) -> list[str]:
    cands: list[tuple[str, int]] = []
    for ci, (_, body) in enumerate(chapters):
        for s in _split_sentences(body):
            s = s.replace("\n", "")
            if 20 <= len(s) <= 90 and name_re.search(s):
                cands.append((s, ci))
    rng = random.Random(42)
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
    for ci, (title, body) in enumerate(chapters):
        cleaned = cleaner.clean(body, doc_id="book")
        cleaned.chapter_title = title
        blocks = chunker.chunk(cleaned, doc_id="book", chapter_index=ci)
        for b in blocks:
            if getattr(b, "granularity", "") == "child":
                texts.append(b.narrative_text)
    return texts


def _gold_index(anchor: str, texts: list[str]) -> int:
    head = anchor[:10]
    best, best_pos = -1, 10**9
    for i, t in enumerate(texts):
        pos = t.find(head)
        if pos >= 0 and pos < best_pos:
            best_pos, best = pos, i
    if best >= 0:
        return best
    for i, t in enumerate(texts):
        for k in range(10, 5, -1):
            if t.find(anchor[:k]) >= 0:
                return i
    return -1


async def main() -> None:
    import numpy as np

    from src.infrastructure.embedding import Qwen3EmbeddingProvider

    ap = argparse.ArgumentParser()
    ap.add_argument("--md", required=True, type=Path)
    ap.add_argument("--book", required=True)
    ap.add_argument("--names", default="")
    ap.add_argument("--anchors", type=int, default=40)
    ap.add_argument("--sizes", default=",".join(map(str, DEFAULT_SIZES)))
    args = ap.parse_args()

    name_re = re.compile(args.names) if args.names else GENERIC_RE
    sizes = [int(s) for s in args.sizes.split(",") if s.strip()]

    chapters = _load_chapters(args.md)
    anchors = _pick_anchors(chapters, args.anchors, name_re)
    print(f"书 {args.book}：章节 {len(chapters)}，锚点句 {len(anchors)}/{args.anchors}", flush=True)
    if not anchors:
        print("无锚点，检查 --names 专名表", flush=True)
        return
    print("锚点示例:", anchors[:2], flush=True)

    embedder = Qwen3EmbeddingProvider(model_path="models/Qwen3-Embedding-0.6B", device="auto", use_fp16=True)
    qres = await embedder.embed_texts(anchors)
    qvecs = np.array(qres.embeddings)
    qn = np.linalg.norm(qvecs, axis=1, keepdims=True)
    qvecs = qvecs / np.clip(qn, 1e-9, None)
    print("锚点向量完成", flush=True)

    async def run_size(child_chars: int) -> dict:
        texts = _chunk_all(chapters, child_chars)
        avg = sum(len(t) for t in texts) // max(1, len(texts))
        print(f"  child={child_chars}: {len(texts)} 块, 平均 {avg} 字", flush=True)

        vecs = np.array((await embedder.embed_texts(texts)).embeddings)
        vn = np.linalg.norm(vecs, axis=1, keepdims=True)
        vecs = vecs / np.clip(vn, 1e-9, None)

        hit1 = hit5 = hit15 = 0
        prec1_sum = 0.0
        for a, qv in zip(anchors, qvecs):
            gold = _gold_index(a, texts)
            if gold < 0:
                continue
            scores = vecs @ qv
            top = np.argsort(-scores)[:15].tolist()
            hit1 += int(top[0] == gold)
            hit5 += int(gold in top[:5])
            hit15 += int(gold in top)
            if top[0] == gold:
                prec1_sum += len(texts[gold]) / max(1, len(texts[top[0]]))
        n = len(anchors)
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
    for size in sizes:
        r = await run_size(size)
        results.append(r)
        print(
            f"  child={size:>4}: 定位@1 {r['hit1_rate']:.1%} ({r['hit1']}/{r['n']})  "
            f"@5 {r['hit5_rate']:.1%} ({r['hit5']}/{r['n']})  @15 {r['hit15_rate']:.1%}  "
            f"top1精准 {r['top1_prec']:.2f}",
            flush=True,
        )

    print(f"\n=== {args.book} 分块扫描汇总（答案句定位，纯向量检索）===")
    print(f"{'child':>6} {'块数':>6} {'均字':>5} {'定位@1':>8} {'定位@5':>8} {'定位@15':>8} {'top1精准':>8}")
    for r in results:
        print(
            f"{r['child_chars']:>6} {r['n_child']:>6} {r['avg_chars']:>5} "
            f"{r['hit1_rate']:>7.1%} {r['hit5_rate']:>7.1%} {r['hit15_rate']:>7.1%} {r['top1_prec']:>8.3f}"
        )

    out = ROOT / "scripts" / "dev" / "eval_dialogue" / "data" / "results" / f"chunk_size_scan_{args.book}.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n结果: {out}")


if __name__ == "__main__":
    asyncio.run(main())
