"""Redialogue — 脱离 ingest 主链路，单独重跑对话提取/归因。

入口：
  - API:  POST /api/v1/agent/novels/{doc_id}/redialogue（novels.py）
  - Job:  job_type="redialogue"（jobs/handlers.py）

流程：
  1. 从 LanceDB 重建 document（narrative blocks → chapters，不碰 ingest）
  2. 读 series inventory（seed_names + candidates；缺失 → InventoryMissingError）
  3. extract_dialogue_for_document（candidate_source 默认 inventory：名单∩本章定位，零 harvest LLM）
  4. 结果文件 → data/redialogue/{doc_id}.json（必写；含 meta + turns 抽样 + 旧 blocks 备份）
  5. 可选写回 LanceDB：删旧 dialogue blocks → index_batch 新 blocks（narrative 不动）

与 ingest 主链路的唯一共享点：dialogue_pipeline.extract_dialogue_for_document
（纯函数入口，LLM 外部注入）。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

logger = logging.getLogger("agent")

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
from src.domain.novel.series_paths import data_root

_REDIALOGUE_DIR = data_root() / "redialogue"
_INVENTORY_DIR = data_root() / "inventories"


class InventoryMissingError(FileNotFoundError):
    """series inventory 文件缺失 —— 需先跑 scripts/dev/rebuild_inventory.py。"""


class DocNotFoundError(FileNotFoundError):
    """LanceDB 中无该 doc 的 narrative blocks，无法重建章节。"""


@dataclass
class RedialogueResult:
    doc_id: str
    chapters: int = 0
    meta: dict = field(default_factory=dict)
    result_path: str = ""
    written_back: bool = False
    deleted_blocks: int = 0
    new_blocks: int = 0
    llm_calls: int = 0
    turns: int = 0
    blocks: int = 0


def _lance_path() -> Path:
    """读 config.yaml 的 novel_rag.lance_path（相对路径按项目根解析）。"""
    try:
        from src.utils.config import load_config

        cfg = load_config(str(_PROJECT_ROOT / "config.yaml"))
        lp = str((cfg.get("novel_rag") or {}).get("lance_path") or "./data/novel_lance")
    except Exception as exc:  # noqa: BLE001 - config 解析失败回退默认
        logger.warning("lance_path config read failed: %s", exc)
        lp = "./data/novel_lance"
    p = Path(lp)
    return p if p.is_absolute() else _PROJECT_ROOT / p


def rebuild_chapters(doc_id: str) -> list[SimpleNamespace]:
    """从 LanceDB narrative blocks 重建 chapters（title + text，按章节序）。"""
    import lancedb

    db = lancedb.connect(str(_lance_path()))
    table = db.open_table("novel_blocks")
    df = table.to_pandas()
    nq = df[(df["doc_id"] == doc_id) & (df["block_type"] == "narrative")]
    if nq.empty:
        return []

    def ch_idx(gid: str) -> int:
        m = re.search(r"_c(\d+)", gid or "")
        return int(m.group(1)) if m else 0

    nq = nq.assign(_ch=nq["global_id"].map(ch_idx))
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


def load_series_inventory(series_id: str) -> tuple[list[str], list[dict]]:
    """返回 (seed_names, inventory_characters)。characters 为 dict（兼容 quota）。"""
    inv_path = _INVENTORY_DIR / f"{series_id}.json"
    if not inv_path.exists():
        return [], []
    try:
        data = json.loads(inv_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Inventory load failed %s: %s", inv_path, exc)
        return [], []
    seed = [str(n).strip() for n in (data.get("seed_names") or []) if str(n).strip()]
    chars = [c for c in (data.get("candidates") or []) if isinstance(c, dict)]
    return seed, chars


def _turns_snapshot(pipe: Any, sample_n: int = 0) -> list[dict]:
    """抽样 turns（随机种子固定，便于复现）供人工复核 speaker。"""
    import random

    turns: list[dict] = []
    for b in getattr(pipe, "blocks", []) or []:
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
    if sample_n > 0 and turns:
        random.seed(20260805)
        random.shuffle(turns)
        turns = turns[:sample_n]
    return turns


def _old_blocks_snapshot(store: Any, doc_id: str) -> list[dict]:
    """旧 dialogue blocks 备份（写回前留存，便于回滚核对）。"""
    old = store.iter_blocks(block_type="dialogue", doc_id=doc_id)
    out: list[dict] = []
    for b in old:
        d = b.to_dict() if hasattr(b, "to_dict") else vars(b)
        out.append(d)
    return out


async def write_back_dialogue(doc_id: str, blocks: list) -> tuple[int, int]:
    """替换该 doc 的 dialogue blocks（narrative/character/qa 不动）。

    Returns (deleted_count, written_count)。
    """
    from src.application.novel.factory import create_novel_store

    store = create_novel_store()
    old = store.iter_blocks(block_type="dialogue", doc_id=doc_id)
    old_ids = [b.global_id for b in old if getattr(b, "global_id", None)]
    deleted = 0
    if old_ids:
        deleted = await store.delete_by_global_ids(old_ids)
    written = await store.index_batch(list(blocks))
    return deleted, written


async def run_redialogue(
    doc_id: str,
    *,
    write_back: bool = False,
    sample_n: int = 0,
    llm_client: Any = None,
    config: dict | None = None,
) -> RedialogueResult:
    """核心编排。doc_id 如 "败犬女主太多了__vol05"。

    Raises:
        InventoryMissingError: series inventory 缺失（需先跑 rebuild_inventory.py）
        DocNotFoundError: LanceDB 无该 doc narrative blocks
    """
    result = RedialogueResult(doc_id=doc_id)
    series_id = doc_id.split("__", 1)[0] if "__" in doc_id else doc_id

    seed, chars = load_series_inventory(series_id)
    if not seed and not chars:
        raise InventoryMissingError(
            f"series inventory not found: data/inventories/{series_id}.json "
            f"(请先运行: python scripts/dev/rebuild_inventory.py {series_id})"
        )

    chapters = rebuild_chapters(doc_id)
    if not chapters:
        raise DocNotFoundError(f"no narrative blocks for {doc_id} in LanceDB")
    result.chapters = len(chapters)
    logger.info(
        "redialogue %s: %d chapters rebuilt, inventory seed=%d chars=%d",
        doc_id,
        len(chapters),
        len(seed),
        len(chars),
    )

    from src.application.novel.dialogue_pipeline import extract_dialogue_for_document
    from src.application.novel.ingest.convert import _build_shared_llm

    owned_llm = llm_client is None
    if owned_llm:
        llm_client = _build_shared_llm(
            temperature=0.0, max_tokens=6144, endpoint="dialogue_extract",
        )
    try:
        pipe = await extract_dialogue_for_document(
            SimpleNamespace(chapters=chapters),
            doc_id,
            llm_client=llm_client,
            volume_seed=seed or None,
            inventory_characters=chars or None,
            config=config,
        )
    finally:
        if owned_llm and llm_client is not None:
            try:
                await llm_client.close()
            except Exception:
                pass

    result.meta = dict(pipe.meta)
    result.llm_calls = int(pipe.meta.get("llm_calls", 0))
    result.turns = int(pipe.meta.get("turns", 0))
    result.blocks = len(list(pipe.blocks))

    # ── 结果文件（必写）──
    _REDIALOGUE_DIR.mkdir(parents=True, exist_ok=True)
    out = _REDIALOGUE_DIR / f"{doc_id}.json"
    payload: dict[str, Any] = {
        "doc_id": doc_id,
        "series_id": series_id,
        "meta": pipe.meta,
        "turns_sample": _turns_snapshot(pipe, sample_n=sample_n),
        "write_back": write_back,
    }
    if write_back:
        try:
            from src.application.novel.factory import create_novel_store

            store = create_novel_store()
            payload["old_blocks_backup"] = _old_blocks_snapshot(store, doc_id)
        except Exception as exc:  # noqa: BLE001 - 备份失败不阻断
            logger.warning("redialogue old-blocks backup failed: %s", exc)
            payload["old_blocks_backup"] = []
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    result.result_path = str(out)

    # ── 可选写回 LanceDB ──
    if write_back and pipe.blocks:
        deleted, written = await write_back_dialogue(doc_id, list(pipe.blocks))
        result.deleted_blocks = deleted
        result.new_blocks = written
        result.written_back = True
        logger.info(
            "redialogue %s: write-back deleted=%d written=%d",
            doc_id,
            deleted,
            written,
        )

    return result
