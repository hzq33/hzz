"""chunk_size_scan.py — 分块大小对向量检索效果的扫描实验。

对同一份重建原文（败犬 vol01，107,780 字），用多档 child 大小分块
（HierarchicalChunker 参数族），每档 embedding 全部 child，然后用
eval_seed 的全部有 gold query 做**纯向量检索**（不经 reranker、不展开），
统计 top-5/top-15 命中率与 NDCG@5。直接回答：小块多大向量检索效果最好。

用法：PYTHONPATH="./venv/Lib/site-packages" ./venv/Scripts/python.exe scripts/dev/eval_dialogue/chunk_size_scan.py
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

SEED_PATH = ROOT / "scripts" / "dev" / "eval_dialogue" / "data" / "eval_seed.json"
RECON_PATH = Path("/tmp/baigou_vol01_recon.txt")
DOC_PREFIX = "败犬女主太多了"

# 扫描档位：child_chars 目标值（min/max 按同比例缩放）
SCAN_SIZES = [80, 100, 150, 200, 300, 400, 600, 800]


def _norm(s: str) -> str:
    return "".join(s.split()).replace("・", "").replace("·", "").replace("、", "").replace("，", "").replace(",", "").replace("—", "")


def _hit_needles(blob: str, variants: dict[str, list[str]]) -> bool:
    return any(any(v and v in blob for v in vlist) for vlist in variants.values())


def _ndcg(blobs: list[str], variants: dict[str, list[str]], k: int = 5) -> float:
    def _gain(blob: str) -> float:
        for idx, (_, vlist) in enumerate(variants.items()):
            if any(v and v in blob for v in vlist):
                return max(3.0 - idx, 1.0)
        return 0.0

    gains = [_gain(b) for b in blobs[:k]]
    dcg = sum((2 ** g - 1) / math.log2(i + 2) for i, g in enumerate(gains) if g > 0)
    ideal = sorted([_gain(b) for b in blobs], reverse=True)[:k]
    idcg = sum((2 ** g - 1) / math.log2(i + 2) for i, g in enumerate(ideal) if g > 0)
    return dcg / idcg if idcg > 0 else 0.0


def _topk(vecs, qvec, k: int) -> list[int]:
    import numpy as np
    scores = vecs @ qvec
    return np.argsort(-scores)[:k].tolist()


async def main() -> None:
    import numpy as np

    from src.infrastructure.embedding import Qwen3EmbeddingProvider

    full = RECON_PATH.read_text(encoding="utf-8")
    print(f"原文 {len(full)} 字", flush=True)

    seed = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    cases = [
        c for c in seed["cases"]
        if c.get("doc_prefix") == DOC_PREFIX and c.get("gold_variants")
    ]
    print(f"评估 query（败犬 + 有 gold）: {len(cases)}", flush=True)

    embedder = Qwen3EmbeddingProvider(model_path="models/Qwen3-Embedding-0.6B", device="auto", use_fp16=True)
    print("embedding 模型加载完成", flush=True)

    # query 向量（一次算好，各档共用）
    queries = [c["query"] for c in cases]
    qres = await embedder.embed_texts(queries)
    qvecs = np.array(qres.embeddings)
    print(f"query 向量: {qvecs.shape}", flush=True)

    # 单档流程：分块 → 收集 child 文本 → embedding → 检索
    async def run_size(child_chars: int) -> dict:
        from src.domain.novel.chunker import CleanedMD, HierarchicalChunker

        # min/max 按比例（保持与默认 150/80/220 同构）
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
        cleaned = CleanedMD(text=full, chapter_title="正文", source_prefix="")
        blocks = chunker.chunk(cleaned, doc_id="baigou", chapter_index=0)
        child_blocks = [b for b in blocks if getattr(b, "granularity", "") == "child"]
        texts = [b.narrative_text for b in child_blocks]
        print(f"  child={child_chars}: {len(texts)} 块, 平均 {sum(len(t) for t in texts)//max(1,len(texts))} 字", flush=True)

        vecs = np.array((await embedder.embed_texts(texts)).embeddings)
        # L2 normalize for cosine
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        vecs = vecs / np.clip(norms, 1e-9, None)

        hit5 = hit15 = 0
        ndcg5 = 0.0
        for c, qv in zip(cases, qvecs):
            qn = np.linalg.norm(qv)
            if qn > 0:
                qv = qv / qn
            top = _topk(vecs, qv, 15)
            blobs = [texts[i] for i in top]
            variants = c["gold_variants"] or {}
            hit5 += int(_hit_needles(" ".join(blobs[:5]), variants))
            hit15 += int(_hit_needles(" ".join(blobs), variants))
            ndcg5 += _ndcg(blobs, variants)
        n = len(cases)
        return {
            "child_chars": child_chars,
            "n_child": len(texts),
            "avg_chars": sum(len(t) for t in texts) // max(1, len(texts)),
            "hit5": hit5, "hit15": hit15, "n": n,
            "hit5_rate": round(hit5 / n, 4), "hit15_rate": round(hit15 / n, 4),
            "ndcg5": round(ndcg5 / n, 4),
        }

    results = []
    for size in SCAN_SIZES:
        r = await run_size(size)
        results.append(r)
        print(
            f"  child={size:>4}: hit@5 {r['hit5_rate']:.1%} ({r['hit5']}/{r['n']})  "
            f"hit@15 {r['hit15_rate']:.1%} ({r['hit15']}/{r['n']})  ndcg@5 {r['ndcg5']:.4f}  "
            f"块数 {r['n_child']} 平均{r['avg_chars']}字",
            flush=True,
        )

    print("\n=== 分块大小扫描汇总（纯向量检索，无 rerank）===")
    print(f"{'child':>6} {'块数':>6} {'均字':>5} {'hit@5':>8} {'hit@15':>8} {'ndcg@5':>8}")
    for r in results:
        print(
            f"{r['child_chars']:>6} {r['n_child']:>6} {r['avg_chars']:>5} "
            f"{r['hit5_rate']:>7.1%} {r['hit15_rate']:>7.1%} {r['ndcg5']:>8.4f}"
        )

    out = ROOT / "scripts" / "dev" / "eval_dialogue" / "data" / "results" / "chunk_size_scan.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n结果: {out}")


if __name__ == "__main__":
    asyncio.run(main())
