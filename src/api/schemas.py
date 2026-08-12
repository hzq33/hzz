"""Pydantic request and response models for the HTTP API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class NovelScope(BaseModel):
    """Retrieval scope — restrict novel RAG to a series and/or specific volumes.

    用于根治跨作品检索污染：前端知识库选择"当前作品"后，
    会话内所有 novel_search 检索都被锁定在该范围内。
    """

    series_id: str | None = Field(
        default=None, description="Series id（如「败犬女主太多了」），锁定整个系列"
    )
    doc_ids: list[str] = Field(
        default_factory=list, description="卷级白名单（格式 「系列名__vol01」）"
    )


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, description="User message")
    session_id: str | None = Field(
        default=None, description="Session ID (auto-generated if empty)"
    )
    novel_scope: NovelScope | None = Field(
        default=None,
        description="Optional retrieval scope restricting novel search to a series/volumes",
    )


class ChatResponse(BaseModel):
    reply: str
    session_id: str


class ToolInfo(BaseModel):
    name: str
    description: str


class ImpersonateRequest(BaseModel):
    character: str = Field(..., min_length=1, description="Character name")
    message: str = Field(..., min_length=1, description="User message to character")
    session_id: str | None = Field(default=None, description="Session ID")
    doc_id: str | None = Field(
        default=None, description="Optional volume lock for retrieval"
    )


class ImpersonateResponse(BaseModel):
    reply: str
    character: str
    session_id: str
    citations: list[dict] = Field(default_factory=list)
    memory_stats: dict | None = Field(default=None, description="上下文用量/压缩统计")


class CharacterInfo(BaseModel):
    name: str
    source: str = ""
    dialogue_count: int = 0
    personality: str = ""
    speaking_style: str = ""
    sample_dialogues: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    has_card: bool = False
    status: str = "candidate"
    character_id: str = ""
    series_id: str = ""
    sample_count: int = 0
    source_chapters: list[str] = Field(default_factory=list)
    source_doc_ids: list[str] = Field(default_factory=list)
    background: str = ""
    mention_count: int = 0
    in_llm_seed: bool = False
    importance: str = "supporting"  # main|supporting|extra（主角/配角/路人，来自 inventory）


class CharacterUpdate(BaseModel):
    """Fields that can be edited by the user."""

    personality: str | None = None
    speaking_style: str | None = None
    background: str | None = None
    catchphrases: list[str] | None = None
    sample_dialogues: list[str] | None = None
    source_work: str | None = None
    relationships: str | None = None
    create_if_missing: bool = False


class CharacterBuildRequest(BaseModel):
    series_id: str = Field(..., min_length=1)
    names: list[str] = Field(..., min_length=1)
    doc_id: str | None = None
    force: bool = False
    resolve: dict[str, str] | None = None
    wait: bool = False


class CharacterMergeRequest(BaseModel):
    """Merge transliteration / near-duplicate names into one survivor within a series."""

    series_id: str = Field(..., min_length=1)
    survivor: str = Field(..., min_length=1)
    names: list[str] = Field(
        ...,
        min_length=2,
        description="All names to merge (must include survivor)",
    )


class StoryAnalysisRequest(BaseModel):
    series_id: str = Field(..., min_length=1)
    doc_id: str | None = None
    force: bool = False
    wait: bool = False
    max_chapters: int | None = Field(
        default=None,
        ge=1,
        le=500,
        description="Override story_analysis.max_chapters for this build",
    )
    extract_foreshadows: bool | None = Field(
        default=None,
        description="Override story_analysis.extract.foreshadows (default false)",
    )


class SeriesRenameRequest(BaseModel):
    series_title: str = Field(
        ..., min_length=1, description="Human-readable series display name"
    )


class ImpersonateRegenerateRequest(BaseModel):
    character: str = Field(..., min_length=1)
    session_id: str = Field(..., min_length=1)
    doc_id: str | None = None
