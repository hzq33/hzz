"""Relation graph aggregation — RelationChange list → {nodes, edges}.

Pure-function module: takes story_analysis RelationChange records and
aggregates them into an undirected graph suitable for frontend rendering.

Two responsibilities:
1. classify_relation_type: map free-text relation_type → standard category
   (family/lover/friend/rival/enemy/mentor/colleague/other) via keyword match,
   with polarity fallback.
2. build_relation_graph: merge per-chapter RelationChange records into edges
   (same character pair → one edge, weight=count, confidence=max, etc.).
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from src.domain.novel.story_analysis.models import RelationChange

# Standard relation categories (used for edge color/grouping in frontend).
CATEGORIES: tuple[str, ...] = (
    "family",
    "lover",
    "friend",
    "rival",
    "enemy",
    "mentor",
    "colleague",
    "other",
)

# Free-text → category mapping (substring match; Chinese novels primarily).
# Order matters only within a category; categories checked in declared order.
_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "family": (
        "父", "母", "兄", "弟", "姐", "妹", "叔", "姑", "姨", "舅",
        "亲戚", "家人", "父女", "母子", "兄妹", "姐弟", "双亲",
        "祖父", "祖母", "女儿", "儿子", "亲子", "血缘",
        "照顾", "照料", "抚养", "养育",
    ),
    "lover": (
        "恋", "爱", "告白", "情侣", "夫妻", "暗恋", "心动", "交往",
        "结婚", "婚约", "女友", "男友", "青梅竹马", "倾心", "钟情",
        "喜欢", "情人",
    ),
    "friend": (
        "朋友", "友谊", "好友", "挚友", "相识", "认识", "同伴",
        "友人", "知己", "伙伴", "熟识", "熟", "依赖", "信赖", "信任",
    ),
    "rival": (
        "对手", "竞争", "宿敌", "劲敌", "竞争者", "较量", "对立", " rivalry",
        "猜疑", "戒备", "试探",
    ),
    "enemy": (
        "敌对", "仇人", "厌恶", "敌人", "仇视", "憎恶", "冲突", "仇恨",
    ),
    "mentor": (
        "师傅", "师父", "老师", "学生", "徒弟", "指导", "教导",
        "师徒", "恩师", "导师", "教导者", "尊敬", "敬重", "教诲",
    ),
    "colleague": (
        "同学", "同事", "同僚", "部下", "上司", "下属", "社长", "会长",
        "部员", "前辈", "后辈", "组员", "团员", "监督", "检查", "合作", "协作",
    ),
}


def classify_relation_type(relation_type: str, polarity: str = "neutral") -> str:
    """Map free-text relation_type to a standard category.

    Keyword substring match first; if no keyword hits, fall back to polarity:
    positive→friend, negative→enemy, neutral→other.
    """
    rt = (relation_type or "").strip()
    if rt:
        for category in CATEGORIES:
            keywords = _CATEGORY_KEYWORDS.get(category, ())
            if any(kw and kw in rt for kw in keywords):
                return category
    pol = (polarity or "neutral").strip().lower()
    if pol == "positive":
        return "friend"
    if pol == "negative":
        return "enemy"
    return "other"


def build_relation_graph(
    relations: list[RelationChange],
    *,
    min_confidence: float = 0.0,
    min_weight: int = 1,
) -> dict[str, Any]:
    """Aggregate RelationChange records into an undirected graph.

    Same character pair (sorted, undirected) → one edge:
      - weight: number of RelationChange records for this pair
      - category: most frequent standardized category among the records
      - polarity: polarity of the highest-confidence record (latest state)
      - confidence: max confidence across records
      - relation_types / summaries / chapters: deduped lists (capped)

    Args:
        relations: raw RelationChange list from story_analysis snapshot.
        min_confidence: drop records below this confidence (default 0.0 = keep all).
        min_weight: drop edges with fewer than this many records (default 1).

    Returns:
        {"nodes": [...], "edges": [...], "stats": {...}}
    """
    # Filter: need both endpoints, distinct, above confidence floor.
    rels = [
        r
        for r in relations
        if r.source
        and r.target
        and r.source != r.target
        and float(r.confidence or 0) >= min_confidence
    ]

    # Group by undirected pair (sorted to canonicalize).
    edge_map: dict[tuple[str, str], list[RelationChange]] = {}
    for r in rels:
        key = tuple(sorted([r.source, r.target]))
        edge_map.setdefault(key, []).append(r)

    edges: list[dict[str, Any]] = []
    node_set: set[str] = set()
    for (a, b), group in edge_map.items():
        node_set.update([a, b])
        if len(group) < min_weight:
            continue
        cats = [classify_relation_type(r.relation_type, r.polarity) for r in group]
        cat_count = Counter(cats)
        primary_cat = cat_count.most_common(1)[0][0]
        # Polarity from highest-confidence record (reflects strongest signal).
        best = max(group, key=lambda r: float(r.confidence or 0))
        summaries = list(dict.fromkeys(r.summary for r in group if r.summary))[:5]
        chapters = list(dict.fromkeys(r.chapter_title for r in group if r.chapter_title))
        relation_types = list(dict.fromkeys(r.relation_type for r in group if r.relation_type))
        orders = [r.chapter_order for r in group if r.chapter_order is not None]
        edges.append(
            {
                "source": a,
                "target": b,
                "category": primary_cat,
                "polarity": best.polarity or "neutral",
                "weight": len(group),
                "confidence": round(max(float(r.confidence or 0) for r in group), 3),
                "relation_types": relation_types,
                "summaries": summaries,
                "chapters": chapters,
                "category_dist": dict(cat_count),
                "first_chapter_order": min(orders) if orders else None,
                "last_chapter_order": max(orders) if orders else None,
            }
        )
    edges.sort(key=lambda e: (-e["weight"], -e["confidence"], e["source"]))

    # Nodes: degree + relation count, sorted by activity.
    edge_count: dict[str, int] = {}
    rel_count: dict[str, int] = {}
    for e in edges:
        for endpoint in (e["source"], e["target"]):
            edge_count[endpoint] = edge_count.get(endpoint, 0) + 1
            rel_count[endpoint] = rel_count.get(endpoint, 0) + e["weight"]
    nodes = sorted(
        (
            {
                "id": n,
                "degree": edge_count.get(n, 0),
                "relations_count": rel_count.get(n, 0),
            }
            for n in node_set
        ),
        key=lambda n: (-n["relations_count"], -n["degree"], n["id"]),
    )

    cat_dist = Counter(e["category"] for e in edges)
    pol_dist = Counter(e["polarity"] for e in edges)
    return {
        "nodes": nodes,
        "edges": edges,
        "stats": {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "relation_count": len(rels),
            "category_dist": dict(cat_dist),
            "polarity_dist": dict(pol_dist),
        },
    }
