"""Narrative Parent/Child expand — Child hits → Parent evidence neighborhood.

Child ``parent_id`` points at Parent ``global_id`` (``{doc}_cXXX_nYYYY``).
Legacy flat blocks (no granularity) expand by ordered neighbor ids.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

_NARR_ID = re.compile(
    r"^(?P<prefix>.+)_c(?P<ch>\d{3})_n(?P<n>\d{4})$"
)
# Child id: {parent_id}__s{iii}
_CHILD_SUFFIX = re.compile(r"__s\d{3}$")


@dataclass
class ExpandedNarrative:
    """One retrieval hit expanded to a contiguous Parent neighborhood."""

    primary_id: str
    score: float
    blocks: list = field(default_factory=list)  # Parent NovelBlocks
    text: str = ""
    chapter_title: str = ""
    doc_id: str = ""
    hit_child_id: str = ""


def parse_narrative_id(global_id: str) -> tuple[str, int, int] | None:
    """Return (prefix_with_doc, chapter_index, narr_index) or None.

    Strips Child ``__sNNN`` suffix so Parent ids parse cleanly.
    """
    gid = _CHILD_SUFFIX.sub("", global_id or "")
    m = _NARR_ID.match(gid)
    if not m:
        return None
    return m.group("prefix"), int(m.group("ch")), int(m.group("n"))


def neighbor_ids(global_id: str, radius: int = 1) -> list[str]:
    """Same-chapter Parent neighbor ids in order (including self)."""
    parsed = parse_narrative_id(global_id)
    if not parsed or radius < 0:
        base = _CHILD_SUFFIX.sub("", global_id or "")
        return [base] if base else []
    prefix, ch, n = parsed
    start = max(0, n - radius)
    end = n + radius
    return [f"{prefix}_c{ch:03d}_n{i:04d}" for i in range(start, end + 1)]


def resolve_parent_id(block: Any) -> str:
    """Parent global_id for a Child or Parent/flat block."""
    parent_id = getattr(block, "parent_id", None) or ""
    if parent_id:
        return parent_id
    gid = getattr(block, "global_id", "") or ""
    gran = getattr(block, "granularity", "") or ""
    if gran == "parent":
        return gid
    # Child id without parent_id set, or flat
    return _CHILD_SUFFIX.sub("", gid)


def expand_narrative_hits(
    store: Any,
    hits: Sequence[Any],
    *,
    radius: int = 1,
    max_expanded_chars: int = 3500,
    chapter_hard_boundary: bool = True,
) -> list[ExpandedNarrative]:
    """Expand Child (or flat) hits into Parent neighborhoods; dedupe by Parent."""
    if not hits:
        return []

    expanded: list[ExpandedNarrative] = []
    covered_parents: set[str] = set()

    for hit in hits:
        block = getattr(hit, "block", None)
        if block is None:
            continue
        gid = getattr(block, "global_id", "") or ""
        if not gid:
            continue

        center_id = resolve_parent_id(block)
        if center_id in covered_parents:
            continue

        if chapter_hard_boundary:
            ids = neighbor_ids(center_id, radius=radius)
        else:
            ids = [center_id]

        blocks = []
        for nid in ids:
            b = store.get_block(nid) if hasattr(store, "get_block") else None
            if b is None and nid == gid:
                b = block
            if b is None and nid == center_id and getattr(block, "granularity", "") == "parent":
                b = block
            if b is None:
                continue
            # Prefer Parent text; skip Child rows in the neighborhood list
            if (getattr(b, "granularity", "") or "") == "child":
                continue
            bt = getattr(b, "block_type", "") or ""
            if bt and bt != "narrative" and not getattr(b, "narrative_text", None):
                continue
            blocks.append(b)
            covered_parents.add(getattr(b, "global_id", "") or nid)

        if not blocks:
            # Fallback: use hit text itself (legacy / missing parent row)
            blocks = [block]
            covered_parents.add(center_id)

        parts = []
        total = 0
        for b in blocks:
            t = (getattr(b, "narrative_text", None) or "").strip()
            if not t:
                continue
            if total and total + len(t) > max_expanded_chars:
                remain = max_expanded_chars - total
                if remain > 80:
                    parts.append(t[:remain] + "…")
                break
            parts.append(t)
            total += len(t)

        text = "\n".join(parts)
        expanded.append(
            ExpandedNarrative(
                primary_id=center_id,
                score=float(getattr(hit, "score", 0) or 0),
                blocks=blocks,
                text=text,
                chapter_title=getattr(blocks[0], "chapter_title", "") or "",
                doc_id=getattr(blocks[0], "doc_id", "") or "",
                hit_child_id=gid if (getattr(block, "granularity", "") or "") == "child" else "",
            )
        )
    return expanded
