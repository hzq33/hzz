"""Rank fusion helpers for multi-path retrieval."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TypeVar

T = TypeVar("T")


def rrf_score(rank: int, k: int = 60) -> float:
    """Reciprocal Rank Fusion score for a 0-based rank."""
    return 1.0 / (k + rank + 1)


def rrf_fuse_id_lists(
    ranked_id_lists: Sequence[Sequence[str]],
    *,
    k: int = 60,
    top_k: int | None = None,
) -> list[tuple[str, float]]:
    """Fuse multiple ranked id lists with RRF.

    Returns ``[(id, score), ...]`` sorted by score descending.
    """
    scores: dict[str, float] = {}
    for ranked in ranked_id_lists:
        for rank, item_id in enumerate(ranked):
            if not item_id:
                continue
            scores[item_id] = scores.get(item_id, 0.0) + rrf_score(rank, k=k)

    ordered = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    if top_k is not None:
        ordered = ordered[:top_k]
    return ordered


def rrf_fuse_hits(
    hit_lists: Sequence[Sequence],
    *,
    k: int = 60,
    top_k: int = 5,
    id_attr: str = "global_id",
):
    """Fuse lists of SearchResultWithBlock-like objects via RRF.

    Ordering uses RRF (``rank_score``). Vector ``similarity`` is preserved
    (max across contributing lists) so UI/confidence never treat RRF as %.
    """
    from src.infrastructure.novel_store import SearchResultWithBlock

    ranked_ids: list[list[str]] = []
    by_id: dict[str, object] = {}
    best_sim: dict[str, float] = {}

    for hits in hit_lists:
        ids: list[str] = []
        for hit in hits:
            block = getattr(hit, "block", None)
            gid = getattr(block, id_attr, None) if block is not None else None
            if not gid:
                continue
            ids.append(gid)
            # Prefer first occurrence for block/channel (usually vector list first)
            by_id.setdefault(gid, hit)
            sim = getattr(hit, "similarity", None)
            if sim is None and getattr(hit, "rank_score", None) is None:
                # Legacy hit: score is similarity
                sim = getattr(hit, "score", None)
            if sim is not None:
                prev = best_sim.get(gid)
                if prev is None or float(sim) > prev:
                    best_sim[gid] = float(sim)
        ranked_ids.append(ids)

    fused = rrf_fuse_id_lists(ranked_ids, k=k, top_k=top_k)
    out: list = []
    for gid, rrf in fused:
        hit = by_id.get(gid)
        if hit is None:
            continue
        sim = best_sim.get(gid)
        out.append(
            SearchResultWithBlock(
                block=hit.block,
                # Prefer similarity as public score; fall back to RRF only if no sim
                score=float(sim) if sim is not None else float(rrf),
                channel=getattr(hit, "channel", ""),
                similarity=sim,
                rank_score=float(rrf),
            )
        )
    return out
