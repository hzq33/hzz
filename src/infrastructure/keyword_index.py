"""Keyword inverted index for coarse filtering before vector search.

Provides BM25-style keyword → global_id ranking for Chinese text.

Indexes:
- Character names (exact)
- Chapter titles (exact)
- Block type (narrative/dialogue/qa/character)
- Style tags
- Content character 2-grams from narrative_text and dialogue text

Retrieval:
- OR recall over all matched terms (character names / chapter / bigrams)
- BM25 scoring with IDF weighting (rare bigrams > common ones)
- Exact name/chapter/style hits get a strong-signal bonus
- Returns global_ids ranked by BM25 score (descending)

Differs from a naive 2-gram inverted index: per-document term frequency
(TF) + document frequency (DF) → IDF weights and score-based ordering,
so keyword hits are actually ranked before RRF fusion.

Usage:
    kw = KeywordsIndex()
    kw.index(block)                           # during ingestion
    ids = kw.search(["利姆露", "利姆", "姆露"])  # at query time
    # → [gid...] sorted by BM25 score (descending)
"""

from __future__ import annotations

import math
from collections import defaultdict

from src.domain.novel.models import NovelBlock

# BM25 parameters (standard defaults)
_K1 = 1.5
_B = 0.75
# Bonus for exact character-name / chapter-title / style-tag matches
# (these are curated strong signals; bigram matches are fuzzy)
_NAME_BONUS = 3.0
# Only index first N chars of content (enough signal, keeps index small)
_CONTENT_CAP = 500


class KeywordsIndex:
    """In-memory BM25 inverted index mapping keywords → ranked global_ids."""

    __slots__ = (
        "_by_char", "_by_chapter", "_by_type", "_by_style",
        "_bigrams", "_tf", "_len", "_all_ids",
    )

    def __init__(self):
        self._by_char: dict[str, set[str]] = defaultdict(set)
        self._by_chapter: dict[str, set[str]] = defaultdict(set)
        self._by_type: dict[str, set[str]] = defaultdict(set)
        self._by_style: dict[str, set[str]] = defaultdict(set)
        self._bigrams: dict[str, set[str]] = defaultdict(set)
        # gid → {bigram: term frequency}  (per-document TF)
        self._tf: dict[str, dict[str, int]] = {}
        # gid → total term count (document length for BM25 length norm)
        self._len: dict[str, int] = {}
        self._all_ids: set[str] = set()

    # ── Indexing ──────────────────────────────────────────

    def index(self, block: NovelBlock) -> None:
        """Index a single block's keywords (upsert by global_id)."""
        gid = block.global_id
        self._all_ids.add(gid)

        # Block type
        if block.block_type:
            self._by_type[block.block_type].add(gid)

        # Characters (exact name match — most important index)
        # all_person 是叙事块角色列表；dialogue 块的说话人在 characters 字段
        names = set(block.all_person or [])
        names |= set(getattr(block, "characters", None) or [])
        if getattr(block, "character_name", ""):
            names.add(block.character_name)
        for name in names:
            if name:
                self._by_char[name].add(gid)

        # Chapter title
        if block.chapter_title:
            self._by_chapter[block.chapter_title].add(gid)

        # Style tags
        for tag in block.style_tags:
            if tag and tag != "未分类":
                self._by_style[tag].add(gid)

        # Content 2-grams with term-frequency (BM25 needs TF + DF)
        text = block.narrative_text or " ".join(
            d.content for d in getattr(block, "dialogues", [])
        ) or ""
        text = text[:_CONTENT_CAP]
        tf: dict[str, int] = {}
        for bg in _bigrams(text):
            tf[bg] = tf.get(bg, 0) + 1
            self._bigrams[bg].add(gid)
        self._tf[gid] = tf
        self._len[gid] = sum(tf.values())

    def index_batch(self, blocks: list[NovelBlock]) -> int:
        """Index multiple blocks. Returns count."""
        for b in blocks:
            self.index(b)
        return len(blocks)

    # ── Search ────────────────────────────────────────────

    def char_names(self) -> list[str]:
        """Character names present in the index (for query keyword extraction)."""
        return sorted(self._by_char.keys())

    def search(
        self,
        keywords: list[str],
        *,
        block_type: str | None = None,
        character: str | None = None,
        chapter: str | None = None,
        limit: int | None = None,
    ) -> list[str]:
        """Return global_ids ranked by BM25 score (descending).

        Recall is OR across all keywords (any term hit recalls the block);
        exact character/chapter/style hits get a strong-signal bonus.
        Filters (block_type / character / chapter) remain AND semantics.

        ``limit`` caps the returned hit count (defence against broad queries
        recalling hundreds of blocks — the caller then pays per-hit work).
        None keeps the historical unbounded behaviour.
        """
        if not keywords:
            candidates = set(self._all_ids)
        else:
            candidates: set[str] = set()
            exact_hits: set[str] = set()  # strong-signal exact matches
            for kw in keywords:
                for idx in (self._by_char, self._by_chapter, self._by_style):
                    s = idx.get(kw)
                    if s:
                        candidates |= s
                        exact_hits |= s
                for bg in _bigrams(kw):
                    s = self._bigrams.get(bg)
                    if s:
                        candidates |= s

        # Filters (AND semantics, unchanged from previous behavior)
        if block_type:
            candidates &= self._by_type.get(block_type, set())
        if character:
            candidates &= self._by_char.get(character, set())
        if chapter:
            candidates &= self._by_chapter.get(chapter, set())

        if not candidates:
            return []

        scored = self._score(keywords, candidates, exact_hits)
        scored.sort(key=lambda x: -x[1])
        if limit is not None and limit > 0:
            scored = scored[:limit]
        return [gid for gid, _ in scored]

    def _score(
        self,
        keywords: list[str],
        candidates: set[str],
        exact_hits: set[str],
    ) -> list[tuple[str, float]]:
        """BM25 scoring over bigram terms extracted from keywords."""
        n = len(self._all_ids) or 1
        avgdl = (sum(self._len.values()) / len(self._len)) if self._len else 1.0

        # Terms: all bigrams of all keywords (deduped, order-preserving)
        terms: list[str] = []
        for kw in keywords:
            for bg in _bigrams(kw):
                if bg not in terms:
                    terms.append(bg)

        out: list[tuple[str, float]] = []
        for gid in candidates:
            tf = self._tf.get(gid, {})
            dl = self._len.get(gid, 0) or 1
            score = 0.0
            for term in terms:
                df = len(self._bigrams.get(term, ()))
                if df == 0:
                    continue
                idf = math.log(1.0 + (n - df + 0.5) / (df + 0.5))
                f = tf.get(term, 0)
                if f == 0:
                    continue
                score += idf * (f * (_K1 + 1.0)) / (
                    f + _K1 * (1.0 - _B + _B * dl / avgdl)
                )
            if gid in exact_hits:
                score += _NAME_BONUS
            if score > 0:
                out.append((gid, score))
        return out

    # ── Management ────────────────────────────────────────

    def clear(self) -> None:
        """Reset all indexes."""
        self._by_char.clear()
        self._by_chapter.clear()
        self._by_type.clear()
        self._by_style.clear()
        self._bigrams.clear()
        self._tf.clear()
        self._len.clear()
        self._all_ids.clear()

    def stats(self) -> dict:
        """Return index statistics."""
        return {
            "total_ids": len(self._all_ids),
            "char_entries": len(self._by_char),
            "chapter_entries": len(self._by_chapter),
            "style_entries": len(self._by_style),
            "bigram_entries": len(self._bigrams),
        }


# ── Helpers ───────────────────────────────────────────────


def _bigrams(text: str) -> list[str]:
    """Character 2-grams for Chinese text (no tokenizer needed).

    "林晚晴看着" → ["林晚", "晚晴", "晴看", "看着"]
    """
    return [text[i:i + 2] for i in range(len(text) - 1)]


def extract_query_keywords(query: str, known_characters: list[str]) -> list[str]:
    """Extract structured keywords from a natural-language query.

    Matches known character names (longest first, ALL matches — not just
    one), chapter references, then falls back to 2-gram extraction.

    Args:
        query: Natural language query string.
        known_characters: List of known character names from the store.

    Returns:
        List of keyword strings (OR recall in BM25 search).
    """
    keywords: list[str] = []
    q = (query or "").strip()
    if not q:
        return []

    # All known character names found in the query (longest first to avoid partials)
    for name in sorted(known_characters, key=len, reverse=True):
        if name and name in q:
            keywords.append(name)

    # Chapter references: "第X章" or "第三章" (Arabic or Chinese numerals)
    import re
    m = re.search(r'第[一二三四五六七八九十百千0-9]+章', q)
    if m:
        keywords.append(m.group(0))

    # If no curated keywords found, fall back to query 2-grams
    if not keywords:
        keywords = _bigrams(q)[:8]

    return keywords
