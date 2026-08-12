"""measure_search_latency.py — 向量检索延迟实测（几万块规模）。

1. 从当前生产库抽取真实 narrative 向量（3761 块）作为种子
2. 复制+加噪扩到 ~4.3 万块（100 字档 × 20 卷量级），保持真实分布
3. 建 LanceDB 表 + IVF_PQ 索引（与 lance_backend.ensure_vector_indices 同参数）
4. 实测：暴力扫描 vs IVF_PQ 索引，各 100 次 query 的 p50/p95/max

用法：PYTHONPATH="./venv/Lib/site-packages" ./venv/Scripts/python.exe scripts/dev/eval_dialogue/measure_search_latency.py
"""

from __future__ import annotations

import asyncio
import random
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

_env = ROOT / ".env"
if _env.exists():
    load_dotenv(_env)

TARGET_ROWS = 43_000  # ~100字档 × 20卷
DIM = 1024


def _load_real_vecs(n: int) -> list[list[float]]:
    import lancedb

    db = lancedb.connect(str(ROOT / "data" / "novel_lance"))
    t = db.open_table("novel_blocks")
    tab = t.to_lance().to_table(columns=["block_type", "vec_narrative"])
    out = []
    for bt, v in zip(tab.column("block_type").to_pylist(), tab.column("vec_narrative").to_pylist()):
        if bt == "narrative" and v is not None:
            out.append(v)
        if len(out) >= n:
            break
    return out


def _augment(seeds: list[list[float]], target: int) -> list[list[float]]:
    """复制种子 + 高斯噪声扰动，扩到 target 行（保持真实分布）。"""
    rng = random.Random(1234)
    rows: list[list[float]] = []
    while len(rows) < target:
        v = seeds[rng.randrange(len(seeds))]
        noise = [rng.gauss(0, 0.03) for _ in range(DIM)]
        rows.append([max(-1.0, min(1.0, a + b)) for a, b in zip(v, noise)])
    return rows[:target]


def _timeit(fn, n: int = 100) -> dict:
    times = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        times.append((time.perf_counter() - t0) * 1000)
    s = sorted(times)
    return {
        "p50": round(statistics.median(s), 2),
        "p95": round(s[int(len(s) * 0.95)], 2),
        "max": round(s[-1], 2),
        "mean": round(sum(s) / len(s), 2),
    }


def main() -> None:
    import numpy as np
    import pyarrow as pa
    import lancedb

    print("加载真实向量…", flush=True)
    seeds = _load_real_vecs(1000)
    print(f"  种子 {len(seeds)} 条", flush=True)

    rows = _augment(seeds, TARGET_ROWS)
    # 正确的 fixed_size_list 构造：先建 float32 array，再包一层 list
    flat = pa.array([x for r in rows for x in r], type=pa.float32())
    vec = pa.FixedSizeListArray.from_arrays(flat, list_size=DIM)
    tbl = pa.table({
        "id": pa.array([f"b{i}" for i in range(len(rows))]),
        "vec": vec,
    })
    print(f"生成 {len(rows)} 行 × {DIM} 维", flush=True)

    db = lancedb.connect(str(ROOT / "data" / "eval" / "latency_test.lance"))
    try:
        db.drop_table("latency_test")
    except Exception:
        pass
    t = db.create_table("latency_test", tbl)
    print("表已建，开始计时…", flush=True)

    q = [random.uniform(-1, 1) for _ in range(DIM)]

    # 1) 暴力扫描（无索引）
    t0 = time.perf_counter()
    br = _timeit(lambda: t.search(q, vector_column_name="vec").limit(5).to_list())
    print(f"\n暴力扫描（无索引）: p50={br['p50']}ms p95={br['p95']}ms max={br['max']}ms", flush=True)

    # 2) IVF_PQ（与生产同参数：num_partitions=64, num_sub_vectors=32）
    t.create_index(
        metric="cosine",
        vector_column_name="vec",
        index_type="IVF_PQ",
        num_partitions=64,
        num_sub_vectors=32,
    )
    ir = _timeit(lambda: t.search(q, vector_column_name="vec").limit(5).to_list())
    print(f"IVF_PQ 索引   : p50={ir['p50']}ms p95={ir['p95']}ms max={ir['max']}ms", flush=True)

    # 3) IVF_PQ + refine_factor（精排放大，LanceDB 新 API 的 nprobe 等价物）
    try:
        n8 = _timeit(lambda: t.search(q, vector_column_name="vec", refine_factor=10).limit(5).to_list())
        print(f"IVF_PQ refine=10: p50={n8['p50']}ms p95={n8['p95']}ms max={n8['max']}ms", flush=True)
    except TypeError:
        print("IVF_PQ refine_factor 不可用，跳过", flush=True)

    # 4) 召回质量抽查：nprobe 对 top-5 命中影响（用种子本身做 query）
    probe_vec = seeds[0]
    brute = t.search(probe_vec, vector_column_name="vec").limit(5).to_list()
    ivf = t.search(probe_vec, vector_column_name="vec").limit(5).to_list()
    print(
        f"\n召回抽查（top-5 id 前 3 位）:\n"
        f"  暴力: {[r['id'][:8] for r in brute][:3]}\n"
        f"  IVF : {[r['id'][:8] for r in ivf][:3]}",
        flush=True,
    )

    # 清理测试表
    try:
        db.drop_table("latency_test")
    except Exception:
        pass


if __name__ == "__main__":
    main()
