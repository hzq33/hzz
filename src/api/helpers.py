"""Shared helpers for API route modules."""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from pathlib import Path

logger = logging.getLogger("agent_server")

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def card_source_meta(card) -> tuple[int, list[str], list[str]]:
    """Return sample count, source chapters, and source document ids."""
    if not card:
        return 0, [], []
    samples = card.sample_dialogues or []
    chapters: list[str] = []
    seen: set[str] = set()
    for dialogue in samples:
        if isinstance(dialogue, dict):
            context = (
                dialogue.get("context")
                or dialogue.get("scene")
                or dialogue.get("chapter_title")
                or ""
            ).strip()
        else:
            context = ""
        if context and context not in seen:
            seen.add(context)
            chapters.append(context)
    docs = list(getattr(card, "source_doc_ids", None) or [])
    return len(samples), chapters[:12], docs


def series_id_from_doc_id(doc_id: str) -> str:
    """Map volume doc_id ``series__vol01`` to series_id ``series``."""
    normalized = (doc_id or "").strip()
    if not normalized:
        return ""
    return re.sub(r"__vol\d+$", "", normalized, flags=re.IGNORECASE) or normalized


async def list_known_series_ids(
    get_imp_store: Callable[[], Awaitable[object]],
) -> list[str]:
    """Enumerate series ids from sidecars and indexed documents.

    Prefer ``series_id`` fields inside JSON (spaces preserved). Never treat
    sanitized filenames (spaces → ``_``) as series ids — that creates ghost
    dropdown entries next to the real series.
    """
    import json

    ids: set[str] = set()
    try:
        from src.application.novel.services.catalog_service import list_catalogs

        for catalog in list_catalogs():
            series_id = (getattr(catalog, "series_id", None) or "").strip()
            if series_id:
                ids.add(series_id)
    except Exception as e:
        logger.warning("list_known_series_ids: catalog scan failed: %s", e)

    for relative in ("data/rosters", "data/inventories", "data/catalogs"):
        folder = _PROJECT_ROOT / relative
        try:
            if not folder.exists():
                continue
            for path in folder.glob("*.json"):
                stem = path.stem
                if stem.endswith(".alias"):
                    continue
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(data, dict):
                    continue
                sid = str(data.get("series_id") or "").strip()
                if sid:
                    ids.add(sid)
        except OSError as e:
            logger.warning("list_known_series_ids: scan %s failed: %s", relative, e)

    try:
        store = await get_imp_store()
        for doc_id in store.doc_ids() or []:
            series_id = series_id_from_doc_id(str(doc_id))
            if series_id:
                ids.add(series_id)
    except Exception as e:
        logger.warning("list_known_series_ids: store scan failed: %s", e)

    return sorted(ids)
