"""Impersonation citation model + hit conversion helpers.

Shared by ``impersonation_agent`` and ``impersonation/retrieval`` (avoids circular import).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class Citation:
    """One retrieval hit exposed to the UI for source tracing."""

    channel: str
    score: float = 0.0
    doc_id: str = ""
    block_id: str = ""
    chapter_title: str = ""
    chapter_order: int | None = None
    snippet: str = ""
    role: str = "fact"  # "fact" | "style"
    # Vector similarity for UI %; None when only rank/keyword signal exists.
    similarity: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_evidence(self) -> dict[str, Any]:
        """StoryEvidence-compatible shape (+ channel/score/role/similarity)."""
        sim = self.similarity
        display: float | None
        if sim is not None:
            display = float(sim)
        elif self.score >= 0.15:
            # Legacy path: score was similarity (RRF is typically ≪ 0.15).
            display = float(self.score)
        else:
            display = None
        return {
            "doc_id": self.doc_id,
            "chapter_order": self.chapter_order,
            "chapter_title": self.chapter_title,
            "block_id": self.block_id,
            "snippet": self.snippet,
            "channel": self.channel,
            "score": display,
            "similarity": float(sim) if sim is not None else None,
            "role": self.role,
        }


def _hit_similarity(hit: Any) -> float | None:
    sim = getattr(hit, "similarity", None)
    if sim is not None:
        return float(sim)
    if getattr(hit, "rank_score", None) is None:
        try:
            return float(getattr(hit, "score", 0.0) or 0.0)
        except (TypeError, ValueError):
            return None
    return None


def _hit_to_citation(hit: Any, *, channel: str | None = None, excerpt: int = 400) -> Citation:
    """Convert a search hit into a Citation for SSE / UI source tracing.

    完整链路（search_raw）命中的各通道块都会转成带原文 snippet 的 Citation：
    - narrative: 叙事原文
    - dialogue: 逐句对话（前 6 句）
    - qa: Q/A
    - character: 关系/事件线索或角色人设（修复：此前 character 块 snippet 为空，
      导致前端“事实依据”卡片展示空白）
    """
    block = getattr(hit, "block", None) or hit
    ch = channel or getattr(hit, "channel", "") or getattr(block, "block_type", "") or ""
    sim = _hit_similarity(hit)
    score = float(sim) if sim is not None else float(getattr(hit, "score", 0.0) or 0.0)
    snippet = ""
    if getattr(block, "narrative_text", None):
        snippet = (block.narrative_text or "")[:excerpt]
    elif getattr(block, "dialogues", None):
        parts = []
        for t in (block.dialogues or [])[:6]:
            sp = getattr(t, "speaker", "") or ""
            ct = getattr(t, "content", "") or ""
            parts.append(f"[{sp}] {ct}" if sp else ct)
        snippet = "\n".join(parts)[:excerpt]
    elif getattr(block, "answer", None) or getattr(block, "question", None):
        q = getattr(block, "question", "") or ""
        a = getattr(block, "answer", "") or ""
        snippet = f"Q: {q}\nA: {a}"[:excerpt]
    elif (getattr(block, "block_type", "") or "") == "character":
        snippet = _character_block_snippet(block, excerpt=excerpt)
    return Citation(
        channel=str(ch),
        score=score,
        similarity=sim,
        doc_id=str(getattr(block, "doc_id", "") or ""),
        block_id=str(getattr(block, "global_id", "") or ""),
        chapter_title=str(
            getattr(block, "chapter_title", "")
            or getattr(block, "source", "")
            or getattr(block, "scene", "")
            or ""
        ),
        chapter_order=getattr(block, "chapter_index", None),
        snippet=snippet,
    )


def _character_block_snippet(block: Any, *, excerpt: int = 400) -> str:
    """character 通道块 → 可展示文本（关系/事件线索优先，否则角色人设）。"""
    try:
        from src.application.novel.character_channel_index import (
            format_relation_event_clue,
            is_relation_event_block,
        )

        if is_relation_event_block(block):
            clue = format_relation_event_clue(block, clip=excerpt)
            if clue:
                return clue
        # 普通角色块：人设 / 背景 / 说话风格 / 示例台词
        parts: list[str] = []
        name = str(getattr(block, "character_name", "") or "")
        if not name:
            ident = getattr(block, "character_identity", None)
            if ident is not None:
                try:
                    name = str(getattr(ident, "canonical_name", "") or "")
                except Exception:  # noqa: BLE001
                    name = ""
        if name:
            parts.append(f"角色: {name}")
        for key, label in (("personality", "性格"), ("background", "背景"), ("speech_style", "说话风格")):
            val = str(getattr(block, key, "") or "").strip()
            if val:
                parts.append(f"{label}: {val}")
        if not parts:
            samples = list(getattr(block, "sample_dialogues", None) or [])[:3]
            for s in samples:
                s = str(s or "").strip()
                if s:
                    parts.append(s)
        return "\n".join(parts)[:excerpt]
    except Exception:  # noqa: BLE001
        return ""
