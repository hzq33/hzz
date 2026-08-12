"""Persist per-doc dialogue extraction meta for diagnostics.

See docs/DIALOGUE_UNDERSAMPLE_ATTR_FIX_DESIGN.md Phase 0.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("agent")

_META_DIR = Path(__file__).resolve().parents[3] / "data" / "dialogue_meta"


def _safe_name(doc_id: str) -> str:
    name = re.sub(r"[^\w\u4e00-\u9fff\-]+", "_", (doc_id or "unknown").strip())
    return name[:180] or "unknown"


def dialogue_meta_path(doc_id: str) -> Path:
    return _META_DIR / f"{_safe_name(doc_id)}.json"


def save_dialogue_meta(doc_id: str, meta: dict[str, Any]) -> Path:
    """Write dialogue pipeline meta sidecar (best-effort)."""
    _META_DIR.mkdir(parents=True, exist_ok=True)
    path = dialogue_meta_path(doc_id)
    payload = {
        "doc_id": doc_id,
        "saved_at": datetime.now(UTC).isoformat(),
        "meta": dict(meta or {}),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Wrote dialogue meta sidecar: %s", path)
    return path


def load_dialogue_meta(doc_id: str) -> dict[str, Any] | None:
    path = dialogue_meta_path(doc_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("Failed to load dialogue meta %s: %s", path, e)
        return None
