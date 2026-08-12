"""别名归并验证探针 — 用 LLM 对角色名变体做归并，验证能否产出正确 canonical。

背景：inventory 的 N 元聚类把「温水/温温」错并入「温水佳树」（实际是温水和彦的
别称）；alias.json 70 个变体全部未合并。本探针验证 LLM 能否做对领域判断。

用法（需 venv，PYTHONPATH 覆写；需 .env DEEPSEEK_API_KEY）:
    PYTHONPATH="./venv/Lib/site-packages" ./venv/Scripts/python.exe \
        scripts/dev/analysis/alias_merge_probe.py "败犬女主太多了" [--top 20]

输出:
    scripts/dev/verify/tmp/alias_merge_probe_{series}.json
    （LLM 归并结果 + 每组的变体/例句/理由，供人工复核）
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def collect_variants(series_id: str) -> dict[str, dict]:
    """变体 → {source, mention_count}。来源：inventory candidates aliases + alias.json。"""
    variants: dict[str, dict] = {}

    inv_path = ROOT / "data" / "inventories" / f"{series_id}.json"
    if inv_path.exists():
        inv = json.loads(inv_path.read_text(encoding="utf-8"))
        for c in inv.get("candidates", []):
            name = c.get("name", "")
            if name:
                variants.setdefault(name, {"source": "inventory", "mention_count": c.get("mention_count", 0)})
            for a in c.get("aliases", []) or []:
                if a and a not in variants:
                    variants[a] = {"source": "alias_of_" + name, "mention_count": c.get("mention_count", 0)}

    alias_path = ROOT / "data" / "rosters" / f"{series_id}.alias.json"
    if alias_path.exists():
        alias = json.loads(alias_path.read_text(encoding="utf-8"))
        for e in alias.get("entities", []):
            canon = e.get("canonical_name", "")
            if canon and canon not in variants:
                variants[canon] = {"source": "alias_file", "mention_count": 0}

    return variants


def find_examples(series_id: str, variants: list[str], per: int = 1) -> dict[str, str]:
    """从 LanceDB narrative 找每个变体的出现例句（含上下文，供 LLM 判断指代）。"""
    import lancedb

    db = lancedb.connect(str(ROOT / "data" / "novel_lance"))
    table = db.open_table("novel_blocks")
    df = table.to_pandas()
    prefixes = {series_id, series_id.replace("_", " ")}  # doc_id 用空格分隔
    mask = df["doc_id"].str.startswith(tuple(prefixes), na=False) & (
        df["block_type"] == "narrative"
    )
    docs = df.loc[mask, "narrative_text"].dropna().tolist()
    full_text = "\n".join(str(d) for d in docs)

    examples: dict[str, str] = {}
    for v in variants:
        if not v or len(v) < 2:
            continue
        hits = [m.start() for m in re.finditer(re.escape(v), full_text)]
        if not hits:
            continue
        # 取前 per 个命中，各截一句（±40 字）
        got = []
        for pos in hits[:per]:
            s = max(0, pos - 40)
            e = min(len(full_text), pos + len(v) + 40)
            seg = full_text[s:e].replace("\n", " ")
            if seg not in got:
                got.append(seg)
        examples[v] = " / ".join(got[:per])
    return examples


_PROMPT = """你是轻小说角色别名归并助手。下面是《{series}》全书出现的说话人名变体（含出现例句）。

任务：把【指代同一人】的变体归为一组，每组选一个最规范的全名作 canonical。

关键规则：
- 姓+名 与 姓 或 名 的简称可能指同一人：温水和彦 ↔ 温水 ↔ 温温（canonical=温水和彦）
- 但【不同角色】即使共享姓也不能合并：温水和彦 ≠ 温水佳树（两个不同的人！）
- 敬称变体并入本名：朝云同学 → 朝云千早
- 误写/OCR 错误若明显指同一人则并入（小抜 vs 小拔小夜）
- 无法确认指代的名字单独成组（canonical=原样，variants=[自己]），或标 "unknown"
- 例句用于判断指代（注意台词是谁说的/对谁说的）

变体清单（{n} 个）：
{items}

输出 JSON（只输出 JSON）：
{{"groups": [
  {{"canonical": "温水和彦", "variants": ["温水", "温温"], "reason": "例句显示温温是温水的昵称，与佳树(妹妹)不同人"}}
]}}"""


async def main(series_id: str, top: int, all_variants: bool, batch_size: int) -> int:
    load_env()
    variants = collect_variants(series_id)
    print(f"[probe] 收集变体 {len(variants)} 个")
    ordered = sorted(variants.items(), key=lambda kv: -kv[1].get("mention_count", 0))

    if all_variants:
        focus = [v for v, _ in ordered]
        batches = [focus[i : i + batch_size] for i in range(0, len(focus), batch_size)]
        print(f"[probe] 全量分批: {len(batches)} 批 × ≤{batch_size}")
    else:
        focus = [v for v, _ in ordered[:top]]
        batches = [focus]
        print(f"[probe] 单批 top{len(focus)}")

    examples = find_examples(series_id, focus)
    print(f"[probe] 有例句的变体 {len(examples)}/{len(focus)}")

    from src.application.novel.ingest.convert import _build_shared_llm

    llm = _build_shared_llm(temperature=0.0, max_tokens=2048)
    if llm is None:
        print("[probe] 无 LLM client")
        return 3

    all_groups: list[dict] = []
    try:
        for bi, batch in enumerate(batches, 1):
            items = []
            for v in batch:
                ex = examples.get(v, "（无例句）")
                items.append(f"- {v}  [例句: {ex[:90]}]")
            prompt = _PROMPT.format(series=series_id, n=len(batch), items="\n".join(items))
            raw = await llm.achat(
                [{"role": "user", "content": prompt}], temperature=0.0, max_tokens=2048
            )
            m = re.search(r"\{.*\}", raw or "", re.DOTALL)
            if not m:
                print(f"[probe] 批{bi} LLM 输出无 JSON，跳过")
                continue
            try:
                data = json.loads(m.group())
            except json.JSONDecodeError:
                print(f"[probe] 批{bi} JSON 解析失败，跳过")
                continue
            groups = data.get("groups", [])
            all_groups.extend(groups)
            print(f"[probe] 批{bi}/{len(batches)}: {len(groups)} 组")
    finally:
        try:
            await llm.close()
        except Exception:
            pass

    # 跨批后处理：同 canonical 合并 → 等价校验（E1 变体冲突等）
    from src.domain.novel.alias_merge import merge_duplicate_canonicals, validate_merge_groups

    all_groups = merge_duplicate_canonicals(all_groups)
    errors, warnings = validate_merge_groups(all_groups)
    for w in warnings:
        print(f"  ⚠ {w}")
    if errors:
        print(f"[probe] ⚠ 跨批校验 {len(errors)} 个错误（需人工修正）:")
        for e in errors[:10]:
            print(f"  ✗ {e}")

    out_path = (
        Path(__file__).resolve().parent.parent
        / "verify" / "tmp" / f"alias_merge_probe_{series_id}.json"
    )
    out_path.parent.mkdir(exist_ok=True)
    payload = {
        "series_id": series_id,
        "batch_size": batch_size,
        "groups": all_groups,
        "merge_errors": errors,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n===== LLM 归并结果（{len(all_groups)} 组）=====")
    for g in all_groups:
        print(f"  canonical={g.get('canonical')}  ←  {g.get('variants')}")
    print(f"\n[probe] 完整结果 → {out_path}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="LLM 别名归并验证探针")
    ap.add_argument("series_id", help="系列名，如 败犬女主太多了")
    ap.add_argument("--top", type=int, default=20, help="单批模式取前 N 个高频变体（默认 20）")
    ap.add_argument("--all", action="store_true", help="全量变体分批归并")
    ap.add_argument("--batch-size", type=int, default=30, help="每批变体数（默认 30）")
    args = ap.parse_args()
    sys.exit(asyncio.run(main(args.series_id, args.top, args.all, args.batch_size)))
