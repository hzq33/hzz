"""Story analysis persistence/index helpers — progress, relation index check, persist.

Extracted from the former monolithic ``story_analysis.py``; logic unchanged.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from src.domain.novel.story_analysis.config import save_analysis
from src.domain.novel.story_analysis.models import StoryAnalysisSnapshot

logger = logging.getLogger("agent")

# Progress callback signature: (phase, message, chapter_done, chapter_total, **extra)
ProgressCallback = Callable[[dict[str, Any]], Awaitable[None] | None]


async def _emit_progress(
    on_progress: ProgressCallback | None,
    **payload: Any,
) -> None:
    if on_progress is None:
        return
    try:
        result = on_progress(payload)
        if inspect.isawaitable(result):
            await result
    except Exception as e:
        logger.debug("story analysis progress callback failed: %s", e)


async def _series_has_relation_index(store, series_id: str) -> bool:
    from src.application.novel.character_channel_index import is_relation_event_block
    from src.domain.novel.models import BLOCK_CHARACTER

    if not hasattr(store, "iter_blocks"):
        return False
    try:
        blocks = store.iter_blocks(block_type=BLOCK_CHARACTER) or []
    except Exception:
        return False
    marker = f"story_analysis:{series_id}"
    for b in blocks:
        if not is_relation_event_block(b):
            continue
        src = str(getattr(b, "source", "") or "")
        rel = getattr(b, "relationships", None) or {}
        sid = rel.get("series_id") if isinstance(rel, dict) else ""
        if sid == series_id or marker in src:
            return True
    return False


async def _index_and_persist(store, snap: StoryAnalysisSnapshot) -> StoryAnalysisSnapshot:
    try:
        from src.application.novel.character_channel_index import index_story_analysis

        index_stats = await index_story_analysis(store, snap, replace=True)
        snap.stats["character_channel_index"] = index_stats
        snap.stats.pop("character_channel_index_error", None)
    except Exception as e:
        logger.warning("Story analysis character-channel index failed: %s", e)
        snap.stats["character_channel_index_error"] = str(e)
    save_analysis(snap)
    return snap


