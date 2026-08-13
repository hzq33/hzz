"""On-demand character building — evidence gathering, distill, card persist, jobs.

Split from the former monolithic ``character_on_demand.py`` into:

    models.py   EvidencePack / NormalizeResult / CharacterBuildJob
    jobs.py     SQLite-backed job store
    builder.py  name normalization, evidence gathering, LLM distill
    persist.py  character card persistence
    runner.py   run_build_job / enqueue_builds orchestration

Public API is unchanged.
"""

from __future__ import annotations

from src.domain.novel.character_on_demand.builder import (
    distill_single,
    gather_evidence,
    normalize_name,
)
from src.domain.novel.character_on_demand.jobs import (
    get_job,
    list_jobs,
)
from src.domain.novel.character_on_demand.models import (
    CharacterBuildJob,
    EvidencePack,
    NormalizeResult,
)
from src.domain.novel.character_on_demand.persist import persist_card
from src.domain.novel.character_on_demand.runner import enqueue_builds, run_build_job

__all__ = [
    "EvidencePack",
    "NormalizeResult",
    "CharacterBuildJob",
    "normalize_name",
    "gather_evidence",
    "distill_single",
    "persist_card",
    "run_build_job",
    "enqueue_builds",
    "get_job",
    "list_jobs",
]
