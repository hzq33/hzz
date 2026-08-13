"""Series-level AliasMap — merge honorifics / observed aliases to one character_id.

Bridges NameResolver-style clustering with on-demand character normalize.
Persists at data/rosters/{series_id}.alias.json.
"""

from __future__ import annotations

import json
import logging
import re
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("agent")

from src.domain.novel.series_paths import data_root

_ROSTER_DIR = data_root() / "rosters"


@dataclass
class AliasEntity:
    character_id: str
    canonical_name: str
    aliases: list[str] = field(default_factory=list)
    # 职位/关系称谓（会长、副会长、学姐、老师等），与 aliases 区分：
    # aliases 是名字变体（八奈见、八奈见杏菜），titles 是社会角色称谓。
    # resolve() 优先查 titles，因为称谓是用户最常用的指代方式。
    titles: list[str] = field(default_factory=list)
    confidence: float = 0.8
    source: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AliasEntity:
        return cls(
            character_id=str(data.get("character_id") or ""),
            canonical_name=str(data.get("canonical_name") or ""),
            aliases=list(data.get("aliases") or []),
            titles=list(data.get("titles") or []),
            confidence=float(data.get("confidence") or 0.8),
            source=dict(data.get("source") or {}),
        )

    def all_names(self) -> set[str]:
        """所有可用于过滤的名字（不含 titles，titles 用于 query 解析不用于 metadata 过滤）。"""
        return {self.canonical_name, *self.aliases} - {""}

    def all_surface_forms(self) -> set[str]:
        """所有表面形式（含 titles），用于 query 实体解析。"""
        return {self.canonical_name, *self.aliases, *self.titles} - {""}


@dataclass
class AliasMap:
    series_id: str
    entities: list[AliasEntity] = field(default_factory=list)
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "series_id": self.series_id,
            "entities": [e.to_dict() for e in self.entities],
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AliasMap:
        return cls(
            series_id=str(data.get("series_id") or ""),
            entities=[AliasEntity.from_dict(e) for e in (data.get("entities") or [])],
            updated_at=str(data.get("updated_at") or ""),
        )

    def resolve(self, name: str) -> AliasEntity | None:
        """解析名字/别名/称谓 → AliasEntity。

        查找顺序：canonical_name → titles → aliases → honorific strip。
        titles 优先于 aliases，因为称谓（会长）是用户最常用的指代方式，
        且 titles 数量少、冲突概率低。
        """
        key = (name or "").strip()
        if not key:
            return None
        # 1. 精确匹配 canonical_name
        for e in self.entities:
            if key == e.canonical_name:
                return e
        # 2. 匹配 titles（会长、副会长等职位称谓）
        for e in self.entities:
            if key in e.titles:
                return e
        # 3. 匹配 aliases（名字变体）
        for e in self.entities:
            if key in e.aliases:
                return e
        # 4. honorific strip（同学/小姐/君 等后缀剥离后重试）
        m = re.fullmatch(r"(.+?)(大人|桑|君|酱|醬|同学|小姐|先生)$", key)
        if m:
            base = m.group(1)
            for e in self.entities:
                if base == e.canonical_name or base in e.aliases or base in e.titles:
                    return e
        return None

    def resolve_in_query(self, query: str) -> list[AliasEntity]:
        """扫描 query 中出现的所有实体（含 titles/aliases）。

        用于 EntityResolver 在 query 理解阶段做实体链接。
        按匹配长度降序返回（长名优先，避免"月之木"遮蔽"月之木古都"）。
        """
        if not query:
            return []
        found: list[AliasEntity] = []
        seen_ids: set[str] = set()
        # 按表面形式长度降序排列，优先匹配长名
        pairs: list[tuple[str, AliasEntity]] = []
        for e in self.entities:
            for surface in e.all_surface_forms():
                if surface and len(surface) >= 2:
                    pairs.append((surface, e))
        pairs.sort(key=lambda x: len(x[0]), reverse=True)
        for surface, e in pairs:
            if surface in query and e.canonical_name not in seen_ids:
                found.append(e)
                seen_ids.add(e.canonical_name)
        return found

    def upsert(self, entity: AliasEntity) -> None:
        for i, e in enumerate(self.entities):
            if e.character_id == entity.character_id or e.canonical_name == entity.canonical_name:
                # merge aliases
                merged = sorted(set(e.aliases) | set(entity.aliases) | {entity.canonical_name, e.canonical_name})
                merged = [a for a in merged if a and a != entity.canonical_name]
                entity.aliases = merged
                # merge titles（职位称谓不随名字合并去重逻辑变化）
                entity.titles = sorted(set(e.titles) | set(entity.titles))
                entity.canonical_name = entity.canonical_name or e.canonical_name
                entity.character_id = entity.character_id or e.character_id
                self.entities[i] = entity
                return
        self.entities.append(entity)


def alias_path(series_id: str) -> Path:
    from src.domain.novel.series_paths import alias_json_path

    return alias_json_path(series_id)


def load_alias_map(series_id: str) -> AliasMap | None:
    from src.domain.novel.series_paths import series_stem_aliases

    for stem in series_stem_aliases(series_id):
        path = _ROSTER_DIR / f"{stem}.alias.json"
        if not path.exists():
            continue
        try:
            return AliasMap.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, TypeError) as e:
            logger.warning("Failed to load alias map %s: %s", path, e)
            return None
    return None


def save_alias_map(amap: AliasMap) -> Path:
    path = alias_path(amap.series_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    amap.updated_at = datetime.now(UTC).isoformat()
    payload = json.dumps(amap.to_dict(), ensure_ascii=False, indent=2)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with open(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        Path(tmp_name).replace(path)
    except Exception:
        try:
            Path(tmp_name).unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return path


def build_alias_map_from_roster(series_id: str, roster) -> AliasMap:
    """Seed AliasMap from roster entries (honorific aliases already on entries)."""
    from src.domain.novel.character_roster import character_id_for

    existing = load_alias_map(series_id) or AliasMap(series_id=series_id)
    for e in getattr(roster, "characters", None) or []:
        name = getattr(e, "name", "") or ""
        if not name:
            continue
        cid = getattr(e, "character_id", None) or character_id_for(series_id, name)
        aliases = list(getattr(e, "aliases_observed", None) or [])
        entity = AliasEntity(
            character_id=cid,
            canonical_name=name,
            aliases=aliases,
            confidence=0.85,
            source={
                "corpus": aliases,
                "merged_at": datetime.now(UTC).isoformat(),
            },
        )
        existing.upsert(entity)
    save_alias_map(existing)
    return existing
