"""Dialogue quota models — target and tracker.

Extracted from the former monolithic ``dialogue_quota.py``; logic unchanged.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any

from src.domain.novel.dialogue_quota.merge import (
    DEFAULT_QUOTAS,
    name_edit_distance,
    normalize_character_name,
)


@dataclass
class CharacterQuotaTarget:
    """One character's quota target derived from Inventory."""

    canonical: str
    aliases: list[str] = field(default_factory=list)
    importance: str = "supporting"
    target: int = 50

    @property
    def name_set(self) -> set[str]:
        names = {self.canonical, *self.aliases}
        return {n for n in names if n and len(n) >= 2}


@dataclass
class QuotaTracker:
    """Track indexed turns per character and chapter coverage."""

    targets: dict[str, CharacterQuotaTarget] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    chapters: dict[str, set[int]] = field(default_factory=lambda: defaultdict(set))
    alias_to_canonical: dict[str, str] = field(default_factory=dict)
    indexed_total: int = 0
    skipped_unknown: int = 0
    skipped_quota_full: int = 0
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def resolve(self, speaker: str) -> str | None:
        sp = normalize_character_name(speaker or "")
        if not sp or sp == "未知":
            return None
        if sp in self.alias_to_canonical:
            return self.alias_to_canonical[sp]
        # fuzzy: substring match against known names
        for alias, canon in self.alias_to_canonical.items():
            if len(alias) >= 2 and (alias in sp or sp in alias):
                return canon
        # Near-equal: same length, one-char diff (利姆路 ↔ 利姆露)
        if len(sp) >= 3:
            for alias, canon in self.alias_to_canonical.items():
                if len(alias) != len(sp):
                    continue
                diff = sum(a != b for a, b in zip(alias, sp))
                if diff == 1:
                    return canon
        # P2: edit distance ≤2 for longer names (维尔德拉 ↔ 维鲁多拉)
        if len(sp) >= 4:
            best: str | None = None
            best_d = 3
            for alias, canon in self.alias_to_canonical.items():
                if len(alias) < 4:
                    continue
                d = name_edit_distance(sp, alias)
                if d <= 2 and d < best_d:
                    best_d = d
                    best = canon
            if best is not None:
                return best
        return None

    def importance_of(self, canonical: str) -> str:
        t = self.targets.get(canonical)
        return t.importance if t else "extra"

    def target_of(self, canonical: str) -> int:
        t = self.targets.get(canonical)
        return t.target if t else DEFAULT_QUOTAS["extra"]

    def remaining(self, canonical: str) -> int:
        return max(0, self.target_of(canonical) - self.counts.get(canonical, 0))

    def record(self, canonical: str, chapter_index: int) -> None:
        self.counts[canonical] = self.counts.get(canonical, 0) + 1
        self.chapters[canonical].add(chapter_index)
        self.indexed_total += 1

    def deficit_mains(self) -> list[str]:
        out = []
        for canon, t in self.targets.items():
            if t.importance != "main":
                continue
            if self.counts.get(canon, 0) < t.target:
                out.append(canon)
        return out

    def priority_satisfied(
        self,
        *,
        dialogue_chapter_count: int,
        min_chapter_coverage_main: float = 0.3,
        min_supporting_chapters: int = 4,
    ) -> bool:
        """True when all main+supporting targets and coverage are met."""
        if not self.targets:
            return False
        mains = [t for t in self.targets.values() if t.importance == "main"]
        supporting = [t for t in self.targets.values() if t.importance == "supporting"]
        if not mains and not supporting:
            return False

        for t in mains:
            if self.counts.get(t.canonical, 0) < t.target:
                return False
            need_ch = max(1, int(dialogue_chapter_count * min_chapter_coverage_main))
            if len(self.chapters.get(t.canonical, set())) < need_ch:
                return False
        for t in supporting:
            if self.counts.get(t.canonical, 0) < t.target:
                return False
            if len(self.chapters.get(t.canonical, set())) < min_supporting_chapters:
                # If book has fewer dialogue chapters, relax
                if dialogue_chapter_count >= min_supporting_chapters:
                    return False
        return True

    def snapshot(self) -> dict[str, Any]:
        return {
            canon: {
                "n": self.counts.get(canon, 0),
                "target": self.target_of(canon),
                "importance": self.importance_of(canon),
                "chapters": sorted(self.chapters.get(canon, set())),
            }
            for canon in sorted(self.targets.keys(), key=lambda c: (-self.counts.get(c, 0), c))
        }


