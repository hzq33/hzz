"""attribution_split_impact.py — 验证对话归属分离是否影响检索。

对真实「对话」+ 归属句实例（从原文抽取），构造两种切分：
  A. 分离版（现状）：child1 = 「对话」, child2 = XX说道…（归属独立成块）
  B. 合并版（方案2b）：child1 = 「对话」XX说道…（归属并入对话句）

Query 两组：
  归属型： "XX 说了什么？" / "XX 怎么说？"（依赖 speaker 信号）
  内容型： 对话内容关键词（不依赖 speaker）

指标（每实例）：
  - top1 命中：query 在候选池（同 parent 的所有 child）检索 top-1 是否
    是目标对话块
  - 相似度：query 与目标块的 cosine 相似度

结论判定：若归属型 query 下 合并版 top1 命中率显著 > 分离版，则
归属分离确实伤害检索；反之则影响可忽略。

用法：PYTHONPATH="./venv/Lib/site-packages" ./venv/Scripts/python.exe scripts/dev/eval_dialogue/attribution_split_impact.py
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
SLIME_PATH = Path("/tmp/slime09.md")
N_SAMPLES = 60
SEED = 11

# 归属动词（判断「」后是否跟归属句）——双字动词优先，避免"街道"的"道"
ATTR_VERB = re.compile(r"([\u4e00-\u9fff]{1,6})(说道|问道|答道|喊道|叫道|低语道|心想|回应道|附和道|叹息道|开口道|喃喃道|沉声道|笑道|大声道|轻声道|回答说|开口说)")

# 「对话」+ 归属句：」后紧跟说话人+双字归属动词+标点
ATTR_PAT = re.compile(
    r"(「[^「」]{6,80}」)\s*([\u4e00-\u9fff]{1,6})(说道|问道|答道|喊道|叫道|低语道|心想|回应道|附和道|叹息道|开口道|喃喃道|沉声道|笑道|大声道|轻声道|回答说|开口说)([了着过]?[。！？!?])"
)


def _load_text() -> str:
    if SLIME_PATH.exists():
        return SLIME_PATH.read_text(encoding="utf-8")
    return STORY_PATH.read_text(encoding="utf-8")


def _extract_instances(text: str, n: int) -> list[dict]:
    """抽「对话」+ 归属句实例：」后紧跟说话人 + 双字归属动词 + 标点。"""
    matches = list(ATTR_PAT.finditer(text))
    print(f"  原始匹配: {len(matches)}", flush=True)
    rng = random.Random(SEED)
    rng.shuffle(matches)
    out = []
    for m in matches:
        dia, speaker, verb, punct = m.group(1), m.group(2), m.group(3), m.group(4)
        if speaker in ("他", "她", "我", "你", "大家", "众人", "有人", "其中"):
            continue
        # 归属句 = 说话人+动词+标点（如"利古路多说道。"）
        attr = f"{speaker}{verb}{punct}"
        out.append({"dialogue": dia, "attr": attr, "speaker": speaker})
        if len(out) >= n:
            break
    return out


def _content_query(dialogue: str) -> str:
    """内容型 query：从对话中取 8-16 字片段（去引号）。"""
    core = dialogue.strip("「」『』\"")
    if len(core) <= 8:
        return core
    mid = len(core) // 2
    return core[max(0, mid - 8): mid + 8]


async def main() -> None:
    import numpy as np

    from src.infrastructure.embedding import Qwen3EmbeddingProvider

    text = _load_text()
    insts = _extract_instances(text, N_SAMPLES)
    print(f"实例: {len(insts)}", flush=True)
    if not insts:
        print("无实例，检查正则", flush=True)
        return

    for i in insts[:2]:
        print(f"  sample: {i['dialogue']} {i['attr']}  (speaker={i['speaker']})", flush=True)

    # 构造块集合
    split_blocks: list[str] = []   # 分离版：对话块 + 归属块
    merged_blocks: list[str] = []  # 合并版：对话+归属
    queries_attr: list[str] = []   # 归属型
    queries_cont: list[str] = []   # 内容型
    for inst in insts:
        sp = inst["speaker"]
        dia = inst["dialogue"]
        attr = inst["attr"]
        split_blocks.append(dia)            # 分离：块1 = 对话
        split_blocks.append(attr)           # 分离：块2 = 归属
        merged_blocks.append(f"{dia}{attr}")  # 合并：一块
        queries_attr.append(f"{sp}说了什么")
        queries_cont.append(_content_query(dia))

    embedder = Qwen3EmbeddingProvider(model_path="models/Qwen3-Embedding-0.6B", device="auto", use_fp16=True)
    print("模型加载完成，开始 embedding…", flush=True)

    # 分离版：query 归属型 → 目标块是对话块（index 0,2,4…）
    all_t = split_blocks + merged_blocks + queries_attr + queries_cont
    res = await embedder.embed_texts(all_t)
    vecs = np.array(res.embeddings)
    vn = np.linalg.norm(vecs, axis=1, keepdims=True)
    vecs = vecs / np.clip(vn, 1e-9, None)

    n = len(insts)
    sb = vecs[: 2 * n]              # split blocks (2 per inst)
    mb = vecs[2 * n: 3 * n]         # merged blocks (1 per inst)
    qa = vecs[3 * n: 4 * n]         # attribution queries
    qc = vecs[4 * n: 5 * n]         # content queries

    def _top1_hit(qv, pool, target_idx) -> int:
        scores = pool @ qv
        top = int(np.argmax(scores))
        return int(top == target_idx)

    # 归属型 query：分离版目标 = 对话块(2i)；合并版目标 = 合并块(i)
    hit_a_attr = 0
    sim_a_attr = 0.0
    for i in range(n):
        pool = sb[2 * i: 2 * i + 2]
        hit_a_attr += _top1_hit(qa[i], pool, 0)  # 对话块是目标
        sim_a_attr += float(pool[0] @ qa[i])
    hit_m_attr = 0
    sim_m_attr = 0.0
    for i in range(n):
        hit_m_attr += _top1_hit(qa[i], mb[i:i + 1], 0)
        sim_m_attr += float(mb[i] @ qa[i])

    # 内容型 query：分离版目标 = 对话块(2i)；合并版目标 = 合并块(i)
    hit_a_cont = 0
    hit_m_cont = 0
    sim_a_cont = 0.0
    sim_m_cont = 0.0
    for i in range(n):
        pool = sb[2 * i: 2 * i + 2]
        hit_a_cont += _top1_hit(qc[i], pool, 0)
        sim_a_cont += float(pool[0] @ qc[i])
        hit_m_cont += _top1_hit(qc[i], mb[i:i + 1], 0)
        sim_m_cont += float(mb[i] @ qc[i])

    print("\n=== 归属分离 vs 合并：检索命中对比 ===")
    print(f"{'query类型':<8} {'切分':<6} {'top1命中':<10} {'平均cosine':<12}")
    print(f"{'归属型':<8} {'分离':<6} {hit_a_attr}/{n} ({hit_a_attr/n:.1%})   {sim_a_attr/n:.4f}")
    print(f"{'归属型':<8} {'合并':<6} {hit_m_attr}/{n} ({hit_m_attr/n:.1%})   {sim_m_attr/n:.4f}")
    print(f"{'内容型':<8} {'分离':<6} {hit_a_cont}/{n} ({hit_a_cont/n:.1%})   {sim_a_cont/n:.4f}")
    print(f"{'内容型':<8} {'合并':<6} {hit_m_cont}/{n} ({hit_m_cont/n:.1%})   {sim_m_cont/n:.4f}")

    delta_attr = hit_m_attr - hit_a_attr
    delta_cont = hit_m_cont - hit_a_cont
    print("\n=== 结论 ===")
    print(f"归属型 query：合并 vs 分离 top1 命中差 = {delta_attr:+d}/{n}")
    print(f"内容型 query：合并 vs 分离 top1 命中差 = {delta_cont:+d}/{n}")
    if delta_attr >= 5 or delta_cont >= 5:
        print("→ 归属分离有实际影响（合并收益 ≥5 个 case）")
    else:
        print("→ 归属分离影响可忽略（合并收益 <5 个 case）")

    out = ROOT / "scripts" / "dev" / "eval_dialogue" / "data" / "results" / "attribution_split_impact.json"
    out.write_text(json.dumps({
        "n": n,
        "hit_attr_split": hit_a_attr, "hit_attr_merged": hit_m_attr,
        "hit_cont_split": hit_a_cont, "hit_cont_merged": hit_m_cont,
        "sim_attr_split": round(sim_a_attr / n, 4), "sim_attr_merged": round(sim_m_attr / n, 4),
        "sim_cont_split": round(sim_a_cont / n, 4), "sim_cont_merged": round(sim_m_cont / n, 4),
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n结果: {out}")


if __name__ == "__main__":
    asyncio.run(main())
