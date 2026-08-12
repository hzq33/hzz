"""Character inventory models.

Extracted from the former monolithic ``character_inventory.py``; logic unchanged.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class InventoryCharacter:
    canonical_name: str
    aliases: list[str] = field(default_factory=list)
    importance: str = "supporting"
    mention_count: int = 0
    from_clusters: list[str] = field(default_factory=list)
    # 属性挂接：[{"type": "race", "value": "史莱姆"}, {"type": "role", "value": "魔王"}]
    # type 枚举：role/race/title/location/org/skill_attr/item（V4 实体本体）
    attributes: list[dict] = field(default_factory=list)


@dataclass
class InventoryResult:
    characters: list[InventoryCharacter] = field(default_factory=list)
    dropped: list[dict] = field(default_factory=list)
    draft_clusters: int = 0
    llm_calls: int = 0
    llm_skipped: bool = False
    relations: list[dict] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    def candidate_names(self) -> list[str]:
        out: list[str] = []
        for c in self.characters:
            for n in [c.canonical_name, *c.aliases]:
                n = (n or "").strip()
                if n and n not in out:
                    out.append(n)
        return out


