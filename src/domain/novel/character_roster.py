"""Character roster (L1) — speaker stats from indexed dialogue, no cloud LLM."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("agent")

_ROSTER_DIR = Path(__file__).resolve().parents[3] / "data" / "rosters"


@dataclass
class RosterEntry:
    name: str
    aliases_observed: list[str] = field(default_factory=list)
    dialogue_count: int = 0
    mention_count: int = 0
    chapters: list[str] = field(default_factory=list)
    co_occurrence: dict[str, int] = field(default_factory=dict)
    status: str = "candidate"  # candidate|building|ready|low_evidence|failed
    character_id: str | None = None
    has_card: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RosterEntry:
        return cls(
            name=data.get("name", ""),
            aliases_observed=list(data.get("aliases_observed") or []),
            dialogue_count=int(data.get("dialogue_count") or 0),
            mention_count=int(data.get("mention_count") or 0),
            chapters=list(data.get("chapters") or []),
            co_occurrence=dict(data.get("co_occurrence") or {}),
            status=data.get("status") or "candidate",
            character_id=data.get("character_id"),
            has_card=bool(data.get("has_card")),
        )


@dataclass
class CharacterRoster:
    series_id: str
    doc_ids: list[str] = field(default_factory=list)
    updated_at: str = ""
    characters: list[RosterEntry] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "series_id": self.series_id,
            "doc_ids": list(self.doc_ids),
            "updated_at": self.updated_at,
            "characters": [c.to_dict() for c in self.characters],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CharacterRoster:
        return cls(
            series_id=data.get("series_id", ""),
            doc_ids=list(data.get("doc_ids") or []),
            updated_at=data.get("updated_at") or "",
            characters=[RosterEntry.from_dict(c) for c in (data.get("characters") or [])],
        )

    def find(self, name: str) -> RosterEntry | None:
        key = (name or "").strip()
        if not key:
            return None
        for e in self.characters:
            if e.name == key or key in e.aliases_observed:
                return e
        return None

    def upsert_entry(self, entry: RosterEntry) -> None:
        for i, e in enumerate(self.characters):
            if e.name == entry.name:
                self.characters[i] = entry
                return
        self.characters.append(entry)


def roster_path(series_id: str) -> Path:
    from src.domain.novel.series_paths import roster_json_path

    return roster_json_path(series_id)


def load_roster(series_id: str) -> CharacterRoster | None:
    from src.domain.novel.series_paths import roster_json_path, series_stem_aliases

    # 与 save_roster 同源（series_paths._DATA），避免读写路径不一致
    # （历史 bug：读用模块常量 _ROSTER_DIR，写用 series_paths，测试沙箱下立即暴露）
    base = roster_json_path("").parent
    for stem in series_stem_aliases(series_id):
        path = base / f"{stem}.json"
        if not path.exists():
            continue
        try:
            return CharacterRoster.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, TypeError) as e:
            logger.warning("Failed to load roster %s: %s", path, e)
            return None
    return None


def save_roster(roster: CharacterRoster) -> Path:
    from src.domain.novel.series_paths import roster_json_path, series_stem_aliases

    path = roster_path(roster.series_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    roster.updated_at = datetime.now(UTC).isoformat()
    path.write_text(
        json.dumps(roster.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    # Drop legacy stem duplicates（与读写同源，避免误删真实 data/）
    primary = path.resolve()
    base = roster_json_path("").parent
    for stem in series_stem_aliases(roster.series_id):
        other = (base / f"{stem}.json").resolve()
        if other != primary and other.exists():
            try:
                other.unlink()
            except OSError:
                pass
    logger.info(
        "Saved roster %s (%d characters) → %s",
        roster.series_id,
        len(roster.characters),
        path,
    )
    return path


def character_id_for(series_id: str, canonical_name: str) -> str:
    slug = re.sub(r"[^\w\u4e00-\u9fff]+", "_", canonical_name.strip())
    slug = slug.strip("_") or "unknown"
    return f"{series_id}__{slug}"


def build_roster_from_dialogue_blocks(
    *,
    series_id: str,
    doc_id: str,
    dialogue_blocks: list,
    narrative_blocks: list | None = None,
    min_dialogues: int = 1,
    min_confidence: float = 0.0,
    is_noise_speaker=None,
) -> CharacterRoster:
    """Build / merge L1 roster from dialogue turns (no LLM)."""
    if is_noise_speaker is None:
        try:
            from src.domain.novel.dialogue_span import is_noise_speaker as _noise
            is_noise_speaker = _noise
        except Exception:
            try:
                from src.domain.novel.dialogue_local_llm import is_noise_speaker as _noise
                is_noise_speaker = _noise
            except Exception:
                def is_noise_speaker(name: str) -> bool:  # type: ignore
                    return not name or name in {"未知", "旁白"}

    counts: dict[str, int] = {}
    chapters: dict[str, set[str]] = {}
    co_occ: dict[str, dict[str, int]] = {}
    honorific_alias: dict[str, set[str]] = {}

    for block in dialogue_blocks or []:
        speakers_in_block: list[str] = []
        for turn in getattr(block, "dialogues", None) or []:
            sp = (getattr(turn, "speaker", None) or "").strip()
            conf = float(getattr(turn, "confidence", 1.0) or 0.0)
            if not sp or is_noise_speaker(sp):
                continue
            if conf < min_confidence:
                continue
            counts[sp] = counts.get(sp, 0) + 1
            ch = getattr(block, "chapter_title", "") or ""
            if ch:
                chapters.setdefault(sp, set()).add(ch)
            speakers_in_block.append(sp)
            # 「X大人」→ alias of X if X also appears as speaker elsewhere later
            m = re.fullmatch(r"(.+?)(大人|桑|君|酱|醬)$", sp)
            if m and len(m.group(1)) >= 2:
                honorific_alias.setdefault(m.group(1), set()).add(sp)

        uniq = list(dict.fromkeys(speakers_in_block))
        for i, a in enumerate(uniq):
            for b in uniq[i + 1 :]:
                co_occ.setdefault(a, {})
                co_occ[a][b] = co_occ[a].get(b, 0) + 1
                co_occ.setdefault(b, {})
                co_occ[b][a] = co_occ[b].get(a, 0) + 1

    mentions: dict[str, int] = dict.fromkeys(counts, 0)
    for block in narrative_blocks or []:
        text = getattr(block, "narrative_text", "") or ""
        if not text:
            continue
        for name in list(counts):
            if name in text:
                mentions[name] = mentions.get(name, 0) + text.count(name)

    # Merge honorific speakers into base name when base also speaks
    for sp in list(counts.keys()):
        m = re.fullmatch(r"(.+?)(大人|桑|君|酱|醬)$", sp)
        if not m:
            continue
        base = m.group(1)
        if base in counts and base != sp:
            counts[base] = counts.get(base, 0) + counts.pop(sp, 0)
            honorific_alias.setdefault(base, set()).add(sp)
            if sp in chapters:
                chapters.setdefault(base, set()).update(chapters.pop(sp, set()))
            if sp in co_occ:
                for other, c in co_occ.pop(sp, {}).items():
                    co_occ.setdefault(base, {})
                    co_occ[base][other] = co_occ[base].get(other, 0) + c
                    if other in co_occ and sp in co_occ[other]:
                        co_occ[other][base] = co_occ[other].get(base, 0) + co_occ[other].pop(sp, 0)
            mentions[base] = mentions.get(base, 0) + mentions.pop(sp, 0)

    existing = load_roster(series_id)
    roster = existing or CharacterRoster(series_id=series_id, doc_ids=[])
    if doc_id not in roster.doc_ids:
        roster.doc_ids.append(doc_id)

    # Preserve card status for known names
    prev_by_name = {e.name: e for e in roster.characters}

    new_entries: list[RosterEntry] = []
    for name, cnt in sorted(counts.items(), key=lambda x: (-x[1], x[0])):
        if cnt < min_dialogues:
            continue
        aliases = sorted(honorific_alias.get(name, set()))
        prev = prev_by_name.get(name)
        entry = RosterEntry(
            name=name,
            aliases_observed=sorted(set(aliases) | set(prev.aliases_observed if prev else [])),
            dialogue_count=cnt if not prev else max(cnt, prev.dialogue_count),
            mention_count=mentions.get(name, 0),
            chapters=sorted(chapters.get(name, set()) | set(prev.chapters if prev else [])),
            co_occurrence=dict(
                sorted(
                    (co_occ.get(name) or {}).items(),
                    key=lambda x: -x[1],
                )[:20]
            ),
            status=prev.status if prev else "candidate",
            character_id=prev.character_id if prev else None,
            has_card=prev.has_card if prev else False,
        )
        # If we only saw honorific form as speaker, keep as own entry;
        # aliases_observed on base name still helps normalize later.
        new_entries.append(entry)

    # Keep previous entries that weren't in this doc (other volumes)
    seen = {e.name for e in new_entries}
    for prev in roster.characters:
        if prev.name not in seen:
            new_entries.append(prev)

    new_entries.sort(key=lambda e: (-e.dialogue_count, e.name))
    roster.characters = new_entries
    return roster
