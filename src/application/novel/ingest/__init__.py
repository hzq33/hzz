"""Novel ingest pipeline — unified structured ingestion (package).

Split from the former monolithic ``ingest.py`` into stages:

    convert.py     Phase 0/1/1b  validation, format conversion, preprocessing
    structure.py   Phase 2       chapter structure parsing (regex → LLM → repair)
    blocks.py      Phase 3       narrative/dialogue/qa/character blocks + roster + graph
    indexer.py     Phase 4       vector indexing + catalog sidecar
    coordinator.py               ingest_novel orchestration

Public API is unchanged: ``ingest_novel`` plus the helpers below.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from src.application.novel.ingest.coordinator import ingest_novel
from src.application.novel.ingest.convert import (
    SUPPORTED_MIMES,
    _build_shared_llm,
    _convert_epub,
    _convert_to_md,
    _local_llm_config,
    _preprocess_raw_md,
    _qa_config,
    _select_narrative_for_qa,
    _validate_upload,
    convert_epub,
    convert_to_md,
    preprocess_raw_md,
    ProgressCallback,
)
from src.application.novel.ingest.structure import (
    _detect_chapters_via_llm,
    _parse_structure,
)

logger = logging.getLogger("agent")


@dataclass
class IngestResult:
    """Result of ingesting a document into the novel knowledge base."""

    doc_id: str
    title: str = ""
    source_format: str = ""
    series_id: str = ""
    total_chapters: int = 0
    narrative_blocks: int = 0
    dialogue_blocks: int = 0
    qa_blocks: int = 0
    character_blocks: int = 0
    total_blocks: int = 0
    characters: list[str] = field(default_factory=list)
    graph: Any = None  # CharacterGraph, populated when ingestion succeeds
    error: str | None = None
    skipped: bool = False  # True when dedup detected an already-indexed book

    @property
    def success(self) -> bool:
        return self.error is None


def infer_volume_no(raw: str) -> int | None:
    """Extract 1-based volume number from a filename stem, if present."""
    from src.application.novel.ingest.convert import _infer_volume_no_impl

    return _infer_volume_no_impl(raw)


def clean_series_id(raw: str) -> str:
    """Sanitize a raw series id into a filesystem-safe series key."""
    from src.application.novel.ingest.convert import _clean_series_id_impl

    return _clean_series_id_impl(raw)


def make_doc_id(series_id: str, volume_no: int | None = None) -> str:
    """Build a doc_id from series + optional volume number."""
    from src.application.novel.ingest.convert import _make_doc_id_impl

    return _make_doc_id_impl(series_id, volume_no)


def clean_doc_id(raw: str) -> str:
    """Sanitize a raw doc_id (used for dedupe / re-ingest)."""
    from src.application.novel.ingest.convert import _clean_doc_id_impl

    return _clean_doc_id_impl(raw)


__all__ = [
    "ingest_novel",
    "IngestResult",
    "infer_volume_no",
    "clean_series_id",
    "make_doc_id",
    "clean_doc_id",
    "ProgressCallback",
    "SUPPORTED_MIMES",
    "convert_epub",
    "convert_to_md",
    "preprocess_raw_md",
    "_convert_to_md",
    "_convert_epub",
    "_preprocess_raw_md",
    "_parse_structure",
    "_detect_chapters_via_llm",
    "_select_narrative_for_qa",
]
