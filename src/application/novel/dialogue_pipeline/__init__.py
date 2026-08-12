"""Dialogue extraction pipeline — chapter-level attribution with quota.

Split from the former monolithic ``dialogue_pipeline.py`` into:

    models.py  DialoguePipelineResult
    config.py  attribution config (_attr_config)
    tools.py   seed detection / window candidates / turn-block conversion
    extract.py document entry + chapter-first extract + provider gate
    deepen.py  enrich thin dialogue blocks from store
    legacy.py  legacy window extraction fallback

Public API is unchanged.
"""

from __future__ import annotations

from src.application.novel.dialogue_pipeline.config import _attr_config
from src.application.novel.dialogue_pipeline.deepen import deepen_dialogue_from_store
from src.application.novel.dialogue_pipeline.extract import (
    _provider_needs_llm,
    extract_dialogue_for_document,
)
from src.application.novel.dialogue_pipeline.models import DialoguePipelineResult
from src.application.novel.dialogue_pipeline.tools import (
    _high_confidence_seeds,
    _turns_to_blocks,
    _window_local_candidates,
    assemble_prompt_candidates,
)

__all__ = [
    "DialoguePipelineResult",
    "_attr_config",
    "extract_dialogue_for_document",
    "_provider_needs_llm",
    "deepen_dialogue_from_store",
    "_high_confidence_seeds",
    "_window_local_candidates",
    "_turns_to_blocks",
    "assemble_prompt_candidates",
]
