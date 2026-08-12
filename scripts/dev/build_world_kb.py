"""全量构建世界知识库（data/world_kb.sqlite）—— 从现有 JSON 源。

用法：
    ./venv/Scripts/python.exe scripts/dev/build_world_kb.py [--force]

--force: 删除现有库重建（默认增量：跳过已构建系列）。
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("build_world_kb")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.application.novel.services.world_knowledge_service import (  # noqa: E402
    _DB_PATH,
    _connect,
    _ensure_schema,
    _series_has_data,
    build_series,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build world knowledge SQLite from JSON sources")
    parser.add_argument("--force", action="store_true", help="Drop & rebuild everything")
    args = parser.parse_args()

    if args.force and _DB_PATH.exists():
        _DB_PATH.unlink()
        logger.info("Dropped %s", _DB_PATH)

    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = _connect()
    try:
        _ensure_schema(conn)
    finally:
        conn.close()

    # 系列名 = story_analyses / timelines / lorebooks 目录下的 JSON 文件名（去 .json）
    names: set[str] = set()
    for sub in ("story_analyses", "timelines", "lorebooks"):
        d = ROOT / "data" / sub
        if d.exists():
            names |= {p.stem for p in d.glob("*.json")}

    built, skipped, missing = 0, 0, 0
    for sid in sorted(names):
        if not args.force:
            c = _connect()
            try:
                _ensure_schema(c)
                if _series_has_data(c, sid):
                    skipped += 1
                    continue
            finally:
                c.close()
        if build_series(sid):
            built += 1
        else:
            missing += 1
            logger.warning("  ✗ %s：源 JSON 缺失（跳过）", sid)

    logger.info(
        "完成：built=%d skipped=%d missing=%d → %s",
        built, skipped, missing, _DB_PATH,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
