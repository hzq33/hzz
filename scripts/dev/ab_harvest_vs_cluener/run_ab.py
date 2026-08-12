# -*- coding: utf-8 -*-
"""A/B 对比：CLUENER 本地模型 vs harvest（LLM 章级收割）角色名粗召回。

用法：
    PYTHONIOENCODING=utf-8 ./venv/Scripts/python.exe scripts/dev/ab_harvest_vs_cluener/run_ab.py [--chapters 8] [--device cuda]

输入：LanceDB 中《败犬女主太多了》vol01（前 N 章）
路径 A：CLUENER NER → cluster_mentions（现有角色盘点粗召回）
路径 B：harvest 每章 LLM 说话人收割（现有对话归因粗召回）
gold：手工标注的主要角色表（canonical + aliases）

输出：对比表（候选数 / gold 召回 / 碎片 / 长名 / 耗时 / LLM 调用数）
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

_env = ROOT / ".env"
if _env.exists():
    load_dotenv(_env, encoding="utf-8-sig")

DOC_PREFIX = "败犬女主太多了__vol01"
MAX_CHAPTERS = 8  # 样本章节数（成本控制）

# ── gold 标准（手工标注：败犬女主太多了 主要角色）──────────────
GOLD = [
    ("八奈见杏菜", ["八奈见", "八奈", "杏菜"]),
    ("烧盐柠檬", ["烧盐", "柠檬"]),
    ("白玉莉子", ["白玉", "莉子"]),
    ("温水和彦", ["温水", "温水君"]),
    ("温水佳树", ["佳树"]),
    ("小鞠知花", ["小鞠"]),
    ("志喜屋梦子", ["志喜屋", "志喜屋学姐"]),
    ("马剃天爱星", ["马剃", "天爱星"]),
    ("甘夏古奈美", ["甘夏", "甘夏老师"]),
    ("月之木古都", ["月之木", "月之木学姐"]),
    ("绫野光希", ["绫野", "光希"]),
    ("朝云千早", ["朝云", "千早"]),
    ("玉木慎太郎", ["玉木", "慎太郎"]),
    ("樱井弘人", ["樱井", "弘人"]),
    ("姬宫华恋", ["姬宫", "华恋"]),
    ("权藤亚咲美", ["权藤", "亚咲美"]),
    ("袴田草介", ["草介"]),
    ("橘聪", ["橘"]),
]

# 身份词 / 作家碎片（判断候选是否为噪声）
_ROLE_WORDS = {
    "同学", "小姐", "先生", "老师", "学姐", "学长", "前辈", "后辈", "会长", "书记",
    "店员", "部长", "店主", "经理", "老板", "医生", "护士", "学生", "女生", "男生",
    "少年", "少女", "村民", "路人", "主角", "配角", "众人", "大家", "母亲", "父亲",
}
_WRITER_FRAGS = {"太宰", "三岛", "川端", "芥川", "夏目", "村上", "太宰治", "三岛由纪夫"}

# 匹配用归一化（去空格/标点/隔字符）
def norm(s: str) -> str:
    return re.sub(r"[\s·•、，。！？「」『』“”\"',.:;!?\-—]", "", s or "")


def is_noise(name: str) -> bool:
    n = norm(name)
    if len(n) < 2:
        return True
    if re.fullmatch(r"[\W_]+", n):
        return True
    if n in _WRITER_FRAGS:
        return True
    # 纯身份词（无名字部分）
    if n in _ROLE_WORDS:
        return True
    return False


def match_gold(candidate: str) -> str | None:
    """候选名命中 gold 返回 canonical，否则 None。

    精确匹配优先（避免共享前缀歧义："温水佳树" 必须命中自己，
    而非 "温水和彦" 的 alias "温水"）；子串匹配取最长 ref。
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
    """从 LanceDB 重组 vol01 章节文本 → [(chapter_title, text)]。"""
    from src.infrastructure.lance_backend import LanceDBBackend

    backend = LanceDBBackend(db_path="./data/novel_lance")
    arrow = backend._table.to_arrow()
    mask = [str(d).startswith(DOC_PREFIX) for d in arrow.column("doc_id").to_pylist()]
    sub = arrow.filter(mask)
    titles = sub.column("chapter_title").to_pylist()
    texts = sub.column("narrative_text").to_pylist()

    by_chapter: dict[str, list[str]] = defaultdict(list)
    for t, txt in zip(titles, texts):
        t = str(t or "未命名")
        txt = str(txt or "").strip()
        if txt:
            by_chapter[t].append(txt)

    chapters: list[tuple[str, str]] = []
    for title, parts in by_chapter.items():
        chapters.append((title, "\n".join(parts)))
    # 按章节号排序
    def ch_no(item: tuple[str, str]) -> int:
        m = re.search(r"(\d+)", item[0])
        return int(m.group(1)) if m else 9999
    chapters.sort(key=ch_no)
    return chapters[:MAX_CHAPTERS]


# ── 路径 A：CLUENER ──────────────────────────────────────────
def run_cluener(text: str, device: str) -> tuple[list[str], float]:
    from src.domain.novel.character_ner import cluster_mentions, extract_person_mentions

    t0 = time.time()
    mentions = extract_person_mentions(text, device=device, min_conf=0.3)
    clusters = cluster_mentions(mentions, min_mentions=1, text=text)
    names: list[str] = []
    for c in clusters:
        names.extend(s for s in c.surfaces if s not in names)
    return names, time.time() - t0


# ── 路径 B：harvest ───────────────────────────────────────────
async def run_harvest(chapters: list[tuple[str, str]], llm_client) -> tuple[list[str], int, float]:
    from src.application.novel.dialogue_pipeline.harvest import harvest_chapter_names

    t0 = time.time()
    per_chapter: list[list[str]] = []
    calls = 0
    for title, text in chapters:
        names = await harvest_chapter_names(text, llm_client, max_names=20, max_tokens=512)
        calls += 1
        per_chapter.append(names or [])
        print(f"  [harvest] {title}: {len(names or [])} 名 -> {names}")
    flat: list[str] = []
    for names in per_chapter:
        for n in names:
            if n not in flat:
                flat.append(n)
    return flat, calls, time.time() - t0


# ── 路径 C：harvest ∪ 正则补盲（推荐替代方案）──────────────
def run_combined(chapters, harvest_names):
    from src.domain.novel.speaker_attributor import candidates_from_text

    regex_names: list[str] = []
    for _title, text in chapters:
        for n in candidates_from_text(text, max_n=12):
            if n not in regex_names:
                regex_names.append(n)
    # 并集：harvest 为主，正则补被提及者
    combined = list(harvest_names)
    for n in regex_names:
        if n not in combined:
            combined.append(n)
    return combined, regex_names


# ── 路径 D：LLM 全量盘点（一次扫描全文，替代 CLUENER 的候选方案）──
_LLM_FULL_PROMPT = """你是小说角色盘点助手。从下面的小说正文中提取【角色名】清单。

规则：
- 提取所有被提及的角色：说了话的（说话人）和只被叙述提及的（被讨论者）
- 名字可以是任意长度（中文名、翻译名、外来语名）；敬称变体归一为纯名（"利姆露大人"→"利姆露"）
- 同角色出现简称/全名时都列出（如 "八奈见" 和 "八奈见杏菜"）
- 忽略：他/她/众人/少年 等指代词、旁白、职业身份词（老师/同学/店员/部长）、作者/作家/历史名人
- 不确定的名字不要编造
- 最多 60 个

输出 JSON：{{"names": ["八奈见杏菜", "温水", "绫野光希", ...]}}"""


async def run_llm_full(full_text: str, llm_client) -> tuple[list[str], int, float]:
    import json as _json
    import re as _re

    t0 = time.time()
    raw = await llm_client.achat(
        [
            {"role": "system", "content": _LLM_FULL_PROMPT},
            {"role": "user", "content": full_text[:120000] + "\n\n请输出 JSON。"},
        ],
        temperature=0.0,
        max_tokens=2048,
    )
    names: list[str] = []
    try:
        data = _json.loads(raw or "")
    except Exception:
        m = _re.search(r"\{.*\}", raw or "", _re.DOTALL)
        try:
            data = _json.loads(m.group()) if m else {}
        except Exception:
            data = {}
    for n in (data.get("names") or []):
        n = str(n).strip()
        if n and n not in names:
            names.append(n)
    return names, 1, time.time() - t0


# ── 汇总 ──────────────────────────────────────────────────────
def report(label: str, names: list[str], elapsed: float, extra: str = "") -> None:
    print(f"\n{'='*64}\n【{label}】{extra}\n{'='*64}")
    print(f"候选总数: {len(names)}")
    hits = [n for n in names if match_gold(n)]
    hit_canons = {match_gold(n) for n in hits}
    noise = [n for n in names if is_noise(n)]
    long_names = [n for n in names if len(norm(n)) >= 4]
    print(f"gold 命中候选: {len(hits)}/{len(names)} ({len(hit_canons)}/{len(GOLD)} 个角色)")
    print(f"gold 召回率: {len(hit_canons)/len(GOLD)*100:.1f}%")
    print(f"噪声候选(身份词/作家/碎片): {len(noise)} -> {sorted(noise)[:10]}")
    print(f"长名(≥4字): {len(long_names)} -> {long_names[:12]}")
    print(f"耗时: {elapsed:.1f}s")
    print(f"未命中候选: {sorted(set(names) - set(hits))[:15]}")
    return None


async def main() -> None:
    global MAX_CHAPTERS  # noqa: PLW0603
    parser = argparse.ArgumentParser()
    parser.add_argument("--chapters", type=int, default=MAX_CHAPTERS)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--no-llm", action="store_true", help="跳过 harvest（仅 CLUENER）")
    args = parser.parse_args()
    MAX_CHAPTERS = args.chapters

    print("加载章节文本（LanceDB vol01）...")
    chapters = load_chapters()
    total_chars = sum(len(t) for _, t in chapters)
    print(f"样本: {len(chapters)} 章, {total_chars} 字符")
    full_text = "\n".join(t for _, t in chapters)

    # ── 路径 A：CLUENER ──
    print("\n[路径 A] CLUENER NER 运行中（可能较慢，首次加载模型）...")
    cluener_names, t_a = run_cluener(full_text, args.device)
    report("路径 A: CLUENER 本地模型", cluener_names, t_a, "（现有角色盘点粗召回）")

    if args.no_llm:
        return

    # ── 路径 B：harvest ──
    from src.shared.llm_factory import create_shared_llm
    from src.utils.config import load_config

    config = load_config(str(ROOT / "config.yaml"))
    llm = create_shared_llm(config, temperature=0.0, max_tokens=512)
    print("\n[路径 B] harvest（每章 1 次 LLM 收割）...")
    harvest_names, calls, t_b = await run_harvest(chapters, llm)
    report("路径 B: harvest LLM 章级收割", harvest_names, t_b, f"（LLM 调用 {calls} 次）")

    # ── 路径 C：harvest ∪ 正则补盲 ──
    combined, regex_names = run_combined(chapters, harvest_names)
    report(
        "路径 C: harvest + 正则补盲",
        combined,
        t_b,
        f"（正则候选 {len(regex_names)} 个）",
    )

    # ── 路径 D：LLM 全量盘点 ──
    print("\n[路径 D] LLM 全量盘点（一次扫全文）...")
    llm_full_names, llm_full_calls, t_d = await run_llm_full(full_text, llm)
    report("路径 D: LLM 全量盘点", llm_full_names, t_d, f"（LLM 调用 {llm_full_calls} 次）")

    # ── 汇总对比 ──
    print(f"\n{'='*64}\n对比汇总\n{'='*64}")
    for label, names, t in (
        ("CLUENER", cluener_names, t_a),
        ("harvest", harvest_names, t_b),
        ("LLM全量", llm_full_names, t_d),
    ):
        hits = {match_gold(n) for n in names if match_gold(n)}
        noise = [n for n in names if is_noise(n)]
        print(f"  {label:10} 候选={len(names):3}  gold召回={len(hits)/len(GOLD)*100:5.1f}%  噪声={len(noise):2}  耗时={t:6.1f}s")

    # 各自独有/共有
    a_set, b_set = set(cluener_names), set(harvest_names)
    print(f"\n  CLUENER 独有: {sorted(a_set - b_set)[:12]}")
    print(f"  harvest 独有: {sorted(b_set - a_set)[:12]}")
    print(f"  共有: {sorted(a_set & b_set)[:12]}")

    # 结果落盘
    out = {
        "doc": DOC_PREFIX,
        "chapters": len(chapters),
        "chars": total_chars,
        "gold": GOLD,
        "cluener": {"names": cluener_names, "elapsed": t_a},
        "harvest": {"names": harvest_names, "calls": calls, "elapsed": t_b},
    }
    out_dir = ROOT / "scripts" / "dev" / "ab_harvest_vs_cluener" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    (out_dir / f"{ts}_ab.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n结果已落盘: {out_dir / (ts + '_ab.json')}")


if __name__ == "__main__":
    asyncio.run(main())
