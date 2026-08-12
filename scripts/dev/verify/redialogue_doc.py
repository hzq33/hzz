"""按 doc_id 只重跑 dialogue 通道（保留 narrative）。

对齐设计文档 A4：重抽对话通道，不碰 narrative blocks。

用法（需 venv，注意 PYTHONPATH 覆写）:
    PYTHONPATH="./venv/Lib/site-packages" ./venv/Scripts/python.exe \
        scripts/dev/verify/redialogue_doc.py "败犬女主太多了__vol05" [--dry-run] [--sample 50]

流程:
    1. 从 LanceDB 读该 doc 的 narrative blocks（按 global_id 排序）
    2. 按 global_id 解析章节序，聚合重建 chapters
    3. 读 data/inventories/{series}.json 的 seed_names 作 volume_seed
    4. _build_shared_llm（需 .env 的 DEEPSEEK_API_KEY）
    5. extract_dialogue_for_document → 打印 meta 摘要
    6. --dry-run（默认）：只打印，不写任何文件
       --apply：写 dialogue_meta/{doc_id}.json（不替换 LanceDB blocks）
       --sample N：导出 N 条随机 turn 样本到 scripts/dev/verify/tmp/sample_{doc_id}.json
                  （speaker/content/confidence，供人工抽检 speaker 正确率）
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
# 直接运行脚本时 cwd 不在 sys.path（-m pytest 才自动注入）→ 显式加项目根
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_env() -> None:
    """独立脚本手动加载 .env（server 由 python-dotenv 自动加载，脚本不会）。"""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def rebuild_chapters(doc_id: str):
    """从 LanceDB narrative blocks 重建章节列表（title + text，按章节序）。"""
    import lancedb

    db = lancedb.connect(str(ROOT / "data" / "novel_lance"))
    table = db.open_table("novel_blocks")
    df = table.to_pandas()
    nq = df[(df["doc_id"] == doc_id) & (df["block_type"] == "narrative")]

    def ch_idx(gid: str) -> int:
        m = re.search(r"_c(\d+)", gid or "")
        return int(m.group(1)) if m else 0

    nq = nq.assign(_ch= nq["global_id"].map(ch_idx))
    chapters: dict[int, dict] = {}
    for _, row in nq.sort_values("global_id").iterrows():
        ch = chapters.setdefault(int(row["_ch"]), {"title": "", "text": ""})
        if row.get("chapter_title"):
            ch["title"] = row["chapter_title"]
        ch["text"] += str(row.get("narrative_text") or "")
    return [
        SimpleNamespace(title=v["title"] or f"第{k}章", text=v["text"])
        for k, v in sorted(chapters.items())
    ]


def load_volume_seed(series_id: str) -> tuple[list[str], list[dict]]:
    """返回 (seed_names, inventory_characters)。inventory_characters 供 quota tracker
    建立 canonical/alias 映射（入库归一到 alias 复核后的 canonical）。"""
    inv_path = ROOT / "data" / "inventories" / f"{series_id}.json"
    if not inv_path.exists():
        return [], []
    data = json.loads(inv_path.read_text(encoding="utf-8"))
    seed = [str(n).strip() for n in (data.get("seed_names") or []) if str(n).strip()]
    chars = [c for c in (data.get("candidates") or []) if isinstance(c, dict)]
    return seed, chars


async def main(doc_id: str, apply: bool, sample_n: int, write_back: bool = False) -> int:
    load_env()
    series_id = doc_id.split("__")[0]
    chapters = rebuild_chapters(doc_id)
    if not chapters:
        print(f"[redialogue] 未找到 {doc_id} 的 narrative blocks")
        return 2
    print(f"[redialogue] {doc_id}: 重建 {len(chapters)} 章")
    print(f"[redialogue] 首章: {chapters[0].title} ({len(chapters[0].text)} 字) | "
          f"末章: {chapters[-1].title} ({len(chapters[-1].text)} 字)")

    from src.application.novel.ingest.convert import _build_shared_llm
    from src.application.novel.dialogue_pipeline import extract_dialogue_for_document

    llm = _build_shared_llm(temperature=0.0, max_tokens=4096)
    if llm is None:
        print("[redialogue] 无可用 LLM client（检查 .env DEEPSEEK_API_KEY）")
        return 3

    volume_seed, inventory_chars = load_volume_seed(series_id)
    print(f"[redialogue] volume_seed({len(volume_seed)}): {volume_seed[:8]}...")
    print(f"[redialogue] inventory_characters: {len(inventory_chars)}")

    try:
        pipe = await extract_dialogue_for_document(
            SimpleNamespace(chapters=chapters),
            doc_id,
            llm_client=llm,
            volume_seed=volume_seed or None,
            inventory_characters=inventory_chars or None,
        )
    finally:
        try:
            await llm.close()
        except Exception:
            pass

    m = pipe.meta
    print("\n===== meta 摘要 =====")
    for k in ("provider", "mode", "llm_calls", "turns", "turns_indexed", "blocks",
              "unknown", "vocative_rejected", "unmapped_rejected", "dedupe_dropped",
              "conflicts", "stopped_reason", "skipped_unknown", "skipped_quota_full",
              "harvest_calls", "harvest_total"):
        print(f"  {k}: {m.get(k)}")
    print(f"  不变量 turns >= turns_indexed: {m.get('turns', 0) >= m.get('turns_indexed', 0)}")
    pc = m.get("per_character") or {}
    top = sorted(pc.items(), key=lambda kv: -kv[1]["n"])[:6]
    print("  per_character top6:")
    for name, v in top:
        print(f"    {name}: n={v['n']} target={v.get('target')} imp={v.get('importance')}")

    if apply:
        meta_dir = ROOT / "data" / "dialogue_meta"
        meta_dir.mkdir(parents=True, exist_ok=True)
        out = meta_dir / f"{doc_id}.json"
        out.write_text(
            json.dumps({"doc_id": doc_id, "meta": m}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\n[redialogue] meta 已写入 {out}")
        if write_back:
            from src.application.novel.redialogue import write_back_dialogue

            try:
                deleted, written = await write_back_dialogue(doc_id, list(pipe.blocks))
                print(
                    f"[redialogue] 已写回 LanceDB: 删除旧 {deleted} 块, 写入新 {written} 块"
                )
            except Exception as exc:  # noqa: BLE001
                print(f"[redialogue] 写回失败: {exc}")
    else:
        print("\n[redialogue] dry-run：未写文件（--apply 可写 meta，--write-back 需配合 --apply）")

    if sample_n > 0:
        turns: list[dict] = []
        for b in pipe.blocks:
            ch = getattr(b, "chapter_title", "") or ""
            for d in getattr(b, "dialogues", []) or []:
                turns.append(
                    {
                        "chapter": ch,
                        "speaker": getattr(d, "speaker", ""),
                        "content": getattr(d, "content", ""),
                        "confidence": round(float(getattr(d, "confidence", 0) or 0), 3),
                    }
                )
        if turns:
            random.seed(20260805)
            random.shuffle(turns)
            sample = turns[:sample_n]
            tmp_dir = Path(__file__).resolve().parent / "tmp"
            tmp_dir.mkdir(exist_ok=True)
            sample_path = tmp_dir / f"sample_{doc_id}.json"
            sample_path.write_text(
                json.dumps(sample, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"[redialogue] 抽样 {len(sample)}/{len(turns)} 条 → {sample_path}")
        else:
            print("[redialogue] 无 turns 可抽样")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="按 doc 重跑 dialogue 通道")
    ap.add_argument("doc_id", help="如 败犬女主太多了__vol05")
    ap.add_argument("--apply", action="store_true", help="写入 dialogue_meta（默认 dry-run）")
    ap.add_argument("--write-back", action="store_true", help="写回 LanceDB dialogue blocks（需配合 --apply）")
    ap.add_argument("--sample", type=int, default=0, help="导出 N 条随机 turn 供抽检")
    args = ap.parse_args()
    sys.exit(asyncio.run(main(args.doc_id, args.apply, args.sample, args.write_back)))
