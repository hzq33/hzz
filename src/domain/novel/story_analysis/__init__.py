"""Story analysis — on-demand relation/event index (optional foreshadow).

Split from the former monolithic ``story_analysis.py`` into:

    models.py     data models (evidence / events / foreshadows / relations)
    config.py     settings, persistence paths, name helpers
    map_reduce.py chapter fingerprint + text collection + LLM map
    reduce.py     snapshot reduction + volume merge
    indexer.py    progress / relation-index check / persist
    runner.py     run_story_analysis orchestration

Public API is unchanged.
"""

from __future__ import annotations

from src.domain.novel.story_analysis.config import (
    _build_map_system,
    _canon_name,
    _is_weak_entity_name,
    _load_alias_map,
    _parse_json_object,
    _resolve_run_settings,
    analysis_path,
    load_analysis,
    save_analysis,
    select_chapters_balanced,
    story_analysis_max_tokens,
)
from src.domain.novel.story_analysis.indexer import (
    _emit_progress,
    _index_and_persist,
    _series_has_relation_index,
)
from src.domain.novel.story_analysis.map_reduce import (
    _chapter_fingerprint,
    _collect_chapter_text,
    _map_chapter,
    _map_chapter_with_retry,
)
from src.domain.novel.story_analysis.models import (
    ForeshadowRecord,
    RelationChange,
    StoryAnalysisSnapshot,
    StoryEvent,
    StoryEvidence,
)
from src.domain.novel.story_analysis.reduce import (
    _bind_evidence,
    _reduce_snapshot,
    merge_volume_into_snapshot,
)
from src.domain.novel.story_analysis.runner import run_story_analysis

__all__ = [
    # data models
    "StoryEvidence",
    "StoryEvent",
    "ForeshadowRecord",
    "RelationChange",
    "StoryAnalysisSnapshot",
    # entry points
    "run_story_analysis",
    "load_analysis",
    "save_analysis",
    "analysis_path",
    "story_analysis_max_tokens",
    "select_chapters_balanced",
    "merge_volume_into_snapshot",
]
