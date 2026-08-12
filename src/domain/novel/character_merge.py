"""Same-series character merge — CN transliteration via tone-less pinyin."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

logger = logging.getLogger("agent")

_HONORIFIC_RE = re.compile(r"(大人|桑|君|酱|醬|同学|小姐|先生)$")


def strip_honorific(name: str) -> str:
    return _HONORIFIC_RE.sub("", (name or "").strip())


@lru_cache(maxsize=4096)
def to_pinyin_syllables(name: str) -> tuple[str, ...]:
    """Tone-less pinyin syllables for a display name."""
    key = strip_honorific(name)
    if not key:
        return ()
    from pypinyin import Style, lazy_pinyin

    syllables = lazy_pinyin(key, style=Style.NORMAL, errors="ignore")
    return tuple(s.lower() for s in syllables if s and s.isascii() and s.isalpha())


def edit_distance(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost))
        prev = cur
    return prev[-1]


def name_similarity(a: str, b: str) -> float:
    """0..1 pinyin similarity for transliteration variants (same series suggestions).

    Rules:
    - Same syllable count required (利姆 vs 利姆露 → 0).
    - Exact tone-less pinyin match → 1.0.
    - Joined pinyin string edit distance ≤1 and length ≥6 → 1 - dist/len
      (covers rare romanization typos for ~3+ syllable names).
    - Two-syllable names only match on exact pinyin (蕾姆/雷姆 yes, 蕾姆/拉姆 no).
    """
    sa, sb = to_pinyin_syllables(a), to_pinyin_syllables(b)
    if not sa or not sb or len(sa) != len(sb):
        return 0.0
    if len(sa) < 2:
        return 0.0
    if sa == sb:
        return 1.0
    # Two-syllable: exact only (avoid 蕾姆/拉姆)
    if len(sa) == 2:
        return 0.0
    joined_a = "".join(sa)
    joined_b = "".join(sb)
    if len(joined_a) < 6 or len(joined_b) < 6:
        return 0.0
    dist = edit_distance(joined_a, joined_b)
    if dist == 0:
        return 1.0
    if dist == 1:
        return 1.0 - dist / max(len(joined_a), len(joined_b))
    return 0.0


# Back-compat alias used by older tests / callers
def normalize_for_similarity(name: str) -> str:
    return " ".join(to_pinyin_syllables(name))


@dataclass
class MergeSuggestion:
    names: list[str]
    survivor: str
    score: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "names": list(self.names),
            "survivor": self.survivor,
            "score": round(self.score, 3),
            "reason": self.reason,
        }


@dataclass
class MergeResult:
    series_id: str
    survivor: str
    merged_names: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    character_id: str = ""
    mention_count: int = 0
    dialogue_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "series_id": self.series_id,
            "survivor": self.survivor,
            "merged_names": list(self.merged_names),
            "aliases": list(self.aliases),
            "character_id": self.character_id,
            "mention_count": self.mention_count,
            "dialogue_count": self.dialogue_count,
        }


def _load_name_stats(series_id: str) -> dict[str, dict[str, Any]]:
    """name -> {mention_count, dialogue_count, aliases, character_id, has_card, status}."""
    from src.domain.novel.character_inventory import load_inventory_candidates
    from src.domain.novel.character_roster import load_roster

    stats: dict[str, dict[str, Any]] = {}
    roster = load_roster(series_id)
    if roster:
        for e in roster.characters:
            stats[e.name] = {
                "mention_count": int(e.mention_count or 0),
                "dialogue_count": int(e.dialogue_count or 0),
                "aliases": list(e.aliases_observed or []),
                "character_id": e.character_id or "",
                "has_card": bool(e.has_card),
                "status": e.status or "candidate",
            }
    inventory = load_inventory_candidates(series_id) or {}
    for c in inventory.get("candidates") or []:
        name = (c.get("name") or "").strip()
        if not name:
            continue
        cur = stats.setdefault(
            name,
            {
                "mention_count": 0,
                "dialogue_count": 0,
                "aliases": [],
                "character_id": "",
                "has_card": False,
                "status": "candidate",
            },
        )
        cur["mention_count"] = max(int(cur["mention_count"]), int(c.get("mention_count") or 0))
        cur["aliases"] = sorted(set(cur["aliases"]) | set(c.get("aliases") or []))
    return stats


def suggest_character_merges(
    series_id: str,
    *,
    min_score: float = 0.92,
) -> list[MergeSuggestion]:
    """Cluster same-series names with matching tone-less pinyin (transliteration splits)."""
    stats = _load_name_stats(series_id)
    names = sorted(stats.keys())
    if len(names) < 2:
        return []

    parent = {n: n for n in names}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    pair_scores: dict[tuple[str, str], float] = {}
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            # Pinyin only — do not treat dirty aliases as strong merge edges
            score = name_similarity(a, b)
            if score >= min_score:
                union(a, b)
                pair_scores[tuple(sorted((a, b)))] = score

    groups: dict[str, list[str]] = {}
    for n in names:
        groups.setdefault(find(n), []).append(n)

    suggestions: list[MergeSuggestion] = []
    for members in groups.values():
        if len(members) < 2:
            continue
        members = sorted(
            members,
            key=lambda n: (
                -int(stats[n]["mention_count"]),
                -int(stats[n]["dialogue_count"]),
                -int(stats[n]["has_card"]),
                n,
            ),
        )
        survivor = members[0]
        scores = [
            pair_scores.get(tuple(sorted((a, b))), name_similarity(a, b))
            for i, a in enumerate(members)
            for b in members[i + 1 :]
        ]
        score = max(scores) if scores else min_score
        suggestions.append(
            MergeSuggestion(
                names=members,
                survivor=survivor,
                score=score,
                reason="中译异写（无调拼音相同/近同，同系列）",
            )
        )
    suggestions.sort(key=lambda s: (-s.score, -len(s.names), s.survivor))
    return suggestions


def merge_characters(
    *,
    series_id: str,
    survivor: str,
    merge_names: list[str],
) -> MergeResult:
    """Merge merge_names into survivor within one series (AliasMap + roster + inventory + cards)."""
    from src.domain.character_card import CharacterCard
    from src.domain.novel.alias_map import AliasEntity, AliasMap, load_alias_map, save_alias_map
    from src.domain.novel.catalog import load_catalog
    from src.domain.novel.character_inventory import inventory_path
    from src.domain.novel.character_roster import (
        character_id_for,
        load_roster,
        save_roster,
    )

    # 系列必须已入库（catalog 有卷）——否则会为不存在的系列静默创建
    # roster/alias/inventory sidecar（幽灵数据）。与 run_story_analysis 同款校验。
    catalog = load_catalog(series_id)
    if not catalog or not catalog.volumes:
        raise ValueError(
            f"No catalog for series_id={series_id}; upload/index first"
        )

    survivor = (survivor or "").strip()
    others = sorted(
        {
            n.strip()
            for n in merge_names
            if n and n.strip() and n.strip() != survivor
        }
    )
    if not survivor or not others:
        raise ValueError("survivor and merge_names required (at least one other name)")

    all_names = [survivor, *others]
    stats = _load_name_stats(series_id)
    for n in all_names:
        if n not in stats:
            stats[n] = {
                "mention_count": 0,
                "dialogue_count": 0,
                "aliases": [],
                "character_id": "",
                "has_card": False,
                "status": "candidate",
            }

    cid = (
        stats[survivor].get("character_id")
        or character_id_for(series_id, survivor)
    )
    merged_aliases = set()
    mention = 0
    dialogue = 0
    has_card = False
    status = "candidate"
    for n in all_names:
        s = stats[n]
        mention += int(s["mention_count"] or 0)
        dialogue += int(s["dialogue_count"] or 0)
        merged_aliases |= set(s.get("aliases") or [])
        merged_aliases.add(n)
        has_card = has_card or bool(s.get("has_card"))
        if s.get("status") in {"ready", "low_evidence"}:
            status = s["status"] if status == "candidate" else status
    merged_aliases.discard(survivor)
    aliases = sorted(a for a in merged_aliases if a)

    # Roster
    roster = load_roster(series_id)
    if roster is not None:
        kept = []
        survivor_entry = None
        for e in roster.characters:
            if e.name == survivor:
                survivor_entry = e
            elif e.name in others:
                continue
            else:
                kept.append(e)
        if survivor_entry is None:
            from src.domain.novel.character_roster import RosterEntry

            survivor_entry = RosterEntry(name=survivor, character_id=cid)
        survivor_entry.aliases_observed = sorted(
            set(survivor_entry.aliases_observed or []) | set(aliases)
        )
        survivor_entry.mention_count = max(int(survivor_entry.mention_count or 0), mention)
        survivor_entry.dialogue_count = max(int(survivor_entry.dialogue_count or 0), dialogue)
        survivor_entry.character_id = survivor_entry.character_id or cid
        survivor_entry.has_card = bool(survivor_entry.has_card or has_card)
        if status != "candidate":
            survivor_entry.status = status
        kept.append(survivor_entry)
        kept.sort(key=lambda e: (-e.dialogue_count, -e.mention_count, e.name))
        roster.characters = kept
        save_roster(roster)

    # AliasMap
    amap = load_alias_map(series_id) or AliasMap(series_id=series_id)
    # Drop entities whose canonical is being absorbed
    amap.entities = [
        e
        for e in amap.entities
        if e.canonical_name not in others and e.canonical_name != survivor
    ]
    amap.upsert(
        AliasEntity(
            character_id=cid,
            canonical_name=survivor,
            aliases=aliases,
            confidence=0.95,
            source={
                "type": "manual_merge",
                "merged_names": others,
                "merged_at": datetime.now(UTC).isoformat(),
            },
        )
    )
    save_alias_map(amap)

    # Inventory candidates
    path = inventory_path(series_id)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            by_name = {
                c["name"]: c
                for c in (data.get("candidates") or [])
                if c.get("name")
            }
            survivor_c = by_name.get(survivor) or {
                "name": survivor,
                "aliases": [],
                "mention_count": 0,
                "importance": "supporting",
                "from_clusters": [],
                "in_llm_seed": False,
            }
            for n in others:
                old = by_name.pop(n, None)
                if not old:
                    continue
                survivor_c["mention_count"] = int(survivor_c.get("mention_count") or 0) + int(
                    old.get("mention_count") or 0
                )
                survivor_c["aliases"] = sorted(
                    set(survivor_c.get("aliases") or [])
                    | set(old.get("aliases") or [])
                    | {n}
                )
                if old.get("importance") == "main":
                    survivor_c["importance"] = "main"
            survivor_c["aliases"] = sorted(
                set(survivor_c.get("aliases") or []) | set(aliases) - {survivor}
            )
            survivor_c["in_llm_seed"] = True
            by_name[survivor] = survivor_c
            data["candidates"] = sorted(
                by_name.values(),
                key=lambda x: (-int(x.get("mention_count") or 0), x["name"]),
            )
            data["seed_names"] = [
                c["name"] for c in data["candidates"] if c.get("in_llm_seed")
            ]
            data["updated_at"] = datetime.now(UTC).isoformat()
            path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            logger.warning("inventory merge update failed: %s", exc)

    # Character cards: keep survivor card, fold aliases, remove others
    try:
        card = CharacterCard.load_for_series(series_id, survivor, character_id=cid)
        if card is None:
            for n in others:
                card = CharacterCard.load_for_series(series_id, n)
                if card:
                    break
        if card is not None:
            card.name = survivor
            card.aliases = sorted(set(card.aliases or []) | set(aliases))
            card.series_id = series_id or card.series_id
            card.character_id = cid
            card.source_work = card.source_work or series_id
            CharacterCard.save_for_series(series_id, survivor, card, character_id=cid)

        cache_dir = CharacterCard._CACHE_DIR
        for n in others:
            for path in (
                CharacterCard.cache_path_for(series_id, n),
                cache_dir / f"{n}.json",
                cache_dir / f"{character_id_for(series_id, n)}.json",
            ):
                try:
                    if path.exists():
                        path.unlink()
                except OSError:
                    pass
    except Exception as exc:
        logger.warning("card merge cleanup failed: %s", exc)

    logger.info(
        "Merged characters series=%s survivor=%s others=%s",
        series_id,
        survivor,
        others,
    )
    return MergeResult(
        series_id=series_id,
        survivor=survivor,
        merged_names=others,
        aliases=aliases,
        character_id=cid,
        mention_count=mention,
        dialogue_count=dialogue,
    )
