"""chunk_size_scan_split.py — 分块大小扫描：锚点分组验证（含专名 vs 不含专名）。

解决 chunk_size_scan_book.py 单组锚点的选取偏差：全含专名的句子天然偏向
小块。本脚本抽两组锚点各 N 个：
  - named   : 含专名（角色/地点/事件词）
  - plain   : 不含专名，但含事件动词（说/走/杀/救/看…）、长度 25-90、非纯对话
两组 query 合并 embedding，对每个分块大小分别统计 定位@1/@5/@15。

用法：
  PYTHONPATH="./venv/Lib/site-packages" ./venv/Scripts/python.exe scripts/dev/eval_dialogue/chunk_size_scan_split.py \
      --md /tmp/slime09.md --book slime09 --anchors 60 --sizes 80,100,150,200
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

DEFAULT_SIZES = [80, 100, 150, 200]

# 专名/事件词（named 组）
GENERIC_RE = re.compile(
    r"(利姆露|利姆鲁|维鲁多拉|维鲁德拉|朱菜|紫苑|红丸|苍影|白老|戈毕尔|托蕾妮|米莉姆|雷昂|"
    r"本城正幸|库洛艾|希兹|爱丽丝|勇者|魔王|开国祭|暴风龙|矮人|妖精|哥布林|战争|战役|"
    r"亚瑟|莉娜|维克托|艾琳|雷恩|卡洛琳|玛拉|老首领|马库斯|铁山|小虎|周诚|黑旗军|黑鸦堡|"
    r"灰脊|哨站|永冻之心|雪莲|山口|霍恩|柱子|阿贵|小卡|瘟疫|流寇|暴风雪|背叛|投降|"
    r"死|杀|救|喜欢|爱|恨)"
)

# 事件动词（plain 组：无专名但含动作/心理动词）
ACTION_RE = re.compile(
    r"(说|道|想|问|答|哭|笑|喊|叫|走|跑|跳|推|拉|抓|拿|放|打|杀|救|吃|喝|睡|醒|看|听|"
    r"知|会|能|要|发现|想起|听说|点头|摇头|转身|开口|沉默|离开|回来|继续|停下|抬头|低头|"
    r"望|盯|瞪|皱|握|抱|挡|躲|追|逃|冲|闯|坐|站|躺|翻|合|闭|睁)"
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
        if body and len(body) > 200:
            chapters.append((title, body))
    return chapters


def _is_pure_dialogue(s: str) -> bool:
    return bool(re.match(r"^[「\"『]", s)) and not re.search(r"[\u4e00-\u9fff]{2,}", s.replace("「", "").replace("」", "").replace('"', "").replace("『", "").replace("』", ""))


def _pick_group(chapters: list[tuple[str, str]], n: int, pred) -> list[str]:
    cands: list[tuple[str, int]] = []
    for ci, (_, body) in enumerate(chapters):
        for s in _split_sentences(body):
            s = s.replace("\n", "").strip()
            if not (25 <= len(s) <= 90):
                continue
            if _is_pure_dialogue(s):
                continue
            if pred(s):
                cands.append((s, ci))
    rng = random.Random(7)
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


def _eval_group(anchors: list[str], qvecs, vecs, texts) -> dict:
    import numpy as np

    hit1 = hit5 = hit15 = 0
    for a, qv in zip(anchors, qvecs):
        gold = _gold_index(a, texts)
        if gold < 0:
            continue
        scores = vecs @ qv
        top = np.argsort(-scores)[:15].tolist()
        hit1 += int(top[0] == gold)
        hit5 += int(gold in top[:5])
        hit15 += int(gold in top)
    n = len(anchors)
    return {
        "n": n,
        "hit1": hit1, "hit5": hit5, "hit15": hit15,
        "hit1_rate": round(hit1 / n, 4),
        "hit5_rate": round(hit5 / n, 4),
        "hit15_rate": round(hit15 / n, 4),
    }


async def main() -> None:
    import numpy as np

    from src.infrastructure.embedding import Qwen3EmbeddingProvider

    ap = argparse.ArgumentParser()
    ap.add_argument("--md", required=True, type=Path)
    ap.add_argument("--book", required=True)
    ap.add_argument("--anchors", type=int, default=60)
    ap.add_argument("--sizes", default=",".join(map(str, DEFAULT_SIZES)))
    args = ap.parse_args()

    sizes = [int(s) for s in args.sizes.split(",") if s.strip()]
    chapters = _load_chapters(args.md)

    named = _pick_group(chapters, args.anchors, lambda s: bool(GENERIC_RE.search(s)))
    plain = _pick_group(chapters, args.anchors, lambda s: not GENERIC_RE.search(s) and bool(ACTION_RE.search(s)))
    print(f"书 {args.book}：章节 {len(chapters)} | named {len(named)} 条 / plain {len(plain)} 条", flush=True)
    print("named 示例:", named[:2], flush=True)
    print("plain 示例:", plain[:2], flush=True)
    if not named or not plain:
        print("某一组锚点不足，检查正则", flush=True)
        return

    # 合并 embedding
    all_anchors = named + plain
    embedder = Qwen3EmbeddingProvider(model_path="models/Qwen3-Embedding-0.6B", device="auto", use_fp16=True)
    qres = await embedder.embed_texts(all_anchors)
    qvecs = np.array(qres.embeddings)
    qn = np.linalg.norm(qvecs, axis=1, keepdims=True)
    qvecs = qvecs / np.clip(qn, 1e-9, None)
    q_named = qvecs[: len(named)]
    q_plain = qvecs[len(named):]
    print("锚点向量完成", flush=True)

    async def run_size(child_chars: int) -> dict:
        texts = _chunk_all(chapters, child_chars)
        avg = sum(len(t) for t in texts) // max(1, len(texts))
        print(f"  child={child_chars}: {len(texts)} 块, 平均 {avg} 字", flush=True)

        vecs = np.array((await embedder.embed_texts(texts)).embeddings)
        vn = np.linalg.norm(vecs, axis=1, keepdims=True)
        vecs = vecs / np.clip(vn, 1e-9, None)

        rn = _eval_group(named, q_named, vecs, texts)
        rp = _eval_group(plain, q_plain, vecs, texts)
        print(
            f"    named : @1 {rn['hit1_rate']:.1%} @5 {rn['hit5_rate']:.1%} @15 {rn['hit15_rate']:.1%}",
            flush=True,
        )
        print(
            f"    plain : @1 {rp['hit1_rate']:.1%} @5 {rp['hit5_rate']:.1%} @15 {rp['hit15_rate']:.1%}",
            flush=True,
        )
        return {
            "child_chars": child_chars,
            "n_child": len(texts),
            "avg_chars": avg,
            "named": rn,
            "plain": rp,
        }

    results = []
    for size in sizes:
        r = await run_size(size)
        results.append(r)

    print(f"\n=== {args.book} 锚点分组扫描汇总（纯向量检索）===")
    print(f"{'child':>6} {'块数':>6} {'均字':>5} | {'named@1':>8} {'named@5':>8} {'named@15':>8} | {'plain@1':>8} {'plain@5':>8} {'plain@15':>8}")
    for r in results:
        rn, rp = r["named"], r["plain"]
        print(
            f"{r['child_chars']:>6} {r['n_child']:>6} {r['avg_chars']:>5} | "
            f"{rn['hit1_rate']:>7.1%} {rn['hit5_rate']:>7.1%} {rn['hit15_rate']:>7.1%} | "
            f"{rp['hit1_rate']:>7.1%} {rp['hit5_rate']:>7.1%} {rp['hit15_rate']:>7.1%}"
        )

    out = ROOT / "scripts" / "dev" / "eval_dialogue" / "data" / "results" / f"chunk_size_scan_split_{args.book}.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n结果: {out}")


if __name__ == "__main__":
    asyncio.run(main())
