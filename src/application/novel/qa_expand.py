"""QA → narrative expansion — search QA channel then resolve ref_chunk_ids."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from src.domain.novel.models import BLOCK_NARRATIVE, BLOCK_QA, NovelBlock


@dataclass
class ExpandedQAHit:
    """One QA hit plus its linked narrative passages."""

    qa: NovelBlock
    narratives: list[NovelBlock] = field(default_factory=list)
    score: float = 0.0
    channel: str = BLOCK_QA


async def search_qa_with_narratives(
    store: Any,
    query: str,
    *,
    top_k: int = 3,
    doc_id: str | None = None,
) -> list[ExpandedQAHit]:
    """Search QA channel and expand ``ref_chunk_ids`` into narrative blocks.

    Narratives are globally deduplicated across the result set (first wins).
    """
    hits = await store.search(query, channel=BLOCK_QA, doc_id=doc_id, top_k=top_k)
    if not hits:
        return []

    seen_global: set[str] = set()
    expanded: list[ExpandedQAHit] = []

    for hit in hits:
        block = hit.block
        if not block:
            continue
        if not getattr(block, "question", None) and block.block_type != BLOCK_QA:
            continue

        narratives: list[NovelBlock] = []
        for ref_id in block.ref_chunk_ids or []:
            if not ref_id or ref_id in seen_global:
                continue
            try:
                narr = store.get_block(ref_id)
            except Exception:
                narr = None
            if narr is None:
                continue
            has_text = bool(getattr(narr, "narrative_text", None))
            if narr.block_type and narr.block_type != BLOCK_NARRATIVE and not has_text:
                continue
            if not has_text:
                continue
            narratives.append(narr)
            seen_global.add(ref_id)

        expanded.append(ExpandedQAHit(
            qa=block,
            narratives=narratives,
            score=float(getattr(hit, "score", 0.0) or 0.0),
            channel=getattr(hit, "channel", BLOCK_QA) or BLOCK_QA,
        ))

    return expanded


def format_expanded_hits(
    query: str,
    hits: list[ExpandedQAHit],
    *,
    excerpt_chars: int = 400,
) -> str:
    """Format expanded QA hits for LLM prompt injection."""
    if not hits:
        return ""

    lines = [
        "## 事实参考（QA 定位）",
        f"查询: {query}",
    ]
    for i, hit in enumerate(hits, 1):
        qa = hit.qa
        lines.append(f"\n### 命中 {i} (score={hit.score:.2f})")
        if qa.question:
            lines.append(f"Q: {qa.question}")
        if qa.answer:
            lines.append(f"A: {qa.answer[:200]}")
        if qa.qa_tags:
            lines.append(f"标签: {', '.join(qa.qa_tags)}")
        if not hit.narratives:
            lines.append("（无关联叙事块）")
            continue
        lines.append("原著叙事：")
        for j, narr in enumerate(hit.narratives, 1):
            src = narr.source or narr.chapter_title or f"片段{j}"
            text = (narr.narrative_text or "")[:excerpt_chars]
            lines.append(f"- [{src}]\n{text}")
    return "\n".join(lines)


_FACT_HINTS = re.compile(
    r"(谁|什么|怎么|为什么|哪里|哪裡|何时|什么时候|如何|为何|多少|多久|怎样|"
    r"是不是|是否|哪|的身份|的结局|的真相|的原因|的来历|了解|知道|什么人|认识)"
)


def looks_like_fact_question(text: str) -> bool:
    """Heuristic: user utterance looks like a plot/fact question."""
    t = (text or "").strip()
    if not t:
        return False
    if "？" in t or "?" in t:
        return True
    return bool(_FACT_HINTS.search(t))
