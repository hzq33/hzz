"""Dialogue quota — name normalization, tier settings, merge/collision helpers.

Extracted from the former monolithic ``dialogue_quota.py``; logic unchanged.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

logger = logging.getLogger("agent")

DEFAULT_QUOTAS = {
    "main": 50,
    "supporting": 40,
    "extra": 10,
}

DEFAULT_IMPORTANCE_BLACKLIST = (
    "史莱姆",
    "哥布林",
    # 注：哥布莉娜/哥布达 是个体角色名（非种族），不进黑名单
    "人类",
    "兽人",
    "魔人",
    "魔物",
    "精灵",
    "矮人",
    "恶魔",
    "天使",
    "世界之声",
)


def normalize_character_name(name: str) -> str:
    """Strip NER debris (trailing middle-dot etc.) from person names."""
    s = (name or "").strip()
    while s and s[-1] in "・·．.、,，/／":
        s = s[:-1].rstrip()
    s = s.strip()
    if not s:
        return s
    # 常用译名归一（利姆路 → 利姆露）：主流译名作为 canonical，原始名作 alias
    from src.domain.novel.character_policy import apply_translation_alias

    return apply_translation_alias(s)


def load_importance_tier_settings() -> dict[str, Any]:
    """Load R1/R2 settings from novel_rag.dialogue_attribution (shared by inventory)."""
    from pathlib import Path

    import yaml

    cfg: dict[str, Any] = {
        "merge_alias_collisions": True,
        "merge_near_duplicates": True,
        "near_duplicate_max_distance": 2,
        "near_duplicate_min_len": 4,
        "importance_blacklist": list(DEFAULT_IMPORTANCE_BLACKLIST),
        "main_top_n": 5,
        "supporting_top_n": 20,
        "promote_importance_by_mentions": True,
    }
    cfg_path = Path(__file__).resolve().parents[4] / "config.yaml"
    try:
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        loaded = dict((raw.get("novel_rag") or {}).get("dialogue_attribution") or {})
        if "merge_alias_collisions" in loaded:
            cfg["merge_alias_collisions"] = bool(loaded["merge_alias_collisions"])
        if "merge_near_duplicates" in loaded:
            cfg["merge_near_duplicates"] = bool(loaded["merge_near_duplicates"])
        if "near_duplicate_max_distance" in loaded:
            cfg["near_duplicate_max_distance"] = int(loaded["near_duplicate_max_distance"])
        if "near_duplicate_min_len" in loaded:
            cfg["near_duplicate_min_len"] = int(loaded["near_duplicate_min_len"])
        if isinstance(loaded.get("importance_blacklist"), (list, tuple)):
            cfg["importance_blacklist"] = [
                str(x).strip() for x in loaded["importance_blacklist"] if str(x).strip()
            ]
        if "main_top_n" in loaded:
            cfg["main_top_n"] = int(loaded["main_top_n"])
        if "supporting_top_n" in loaded:
            cfg["supporting_top_n"] = int(loaded["supporting_top_n"])
        if "promote_importance_by_mentions" in loaded:
            cfg["promote_importance_by_mentions"] = bool(
                loaded["promote_importance_by_mentions"]
            )
    except Exception:
        pass
    return cfg


def _char_name(c: Any) -> str:
    if isinstance(c, dict):
        raw = str(c.get("canonical_name") or c.get("name") or "").strip()
    else:
        raw = str(getattr(c, "canonical_name", "") or getattr(c, "name", "") or "").strip()
    return normalize_character_name(raw)


def _char_aliases(c: Any) -> list[str]:
    if isinstance(c, dict):
        raw = list(c.get("aliases") or [])
    else:
        raw = list(getattr(c, "aliases", None) or [])
    name = _char_name(c)
    out: list[str] = []
    for a in raw:
        a = str(a or "").strip()
        if a and a != name and a not in out:
            out.append(a)
    return out


def _char_mentions(c: Any) -> int:
    if isinstance(c, dict):
        return int(c.get("mention_count") or 0)
    return int(getattr(c, "mention_count", 0) or 0)


def _active_score(c: Any, speaker_scores: dict[str, int] | None) -> int:
    """活跃度 = mention_count + 说话活跃章数 × 10。

    修复：仅按 mention 排序会让"被提及多但几乎不开口"的角色（如封印中的
    龙）占用 main 档，而真正有台词的主角（mention 可能不高）被降级。
    说话活跃章数（harvest 跨章出现频次）作为说话条数的代理信号，权重 10
    （1 章活跃 ≈ 10 次提及，使"多章说话"的角色显著升档）。
    """
    m = _char_mentions(c)
    if not speaker_scores:
        return m
    name = _char_name(c)
    active = int(speaker_scores.get(name) or 0)
    return m + active * 10


def _set_char_mentions(c: Any, n: int) -> None:
    if isinstance(c, dict):
        c["mention_count"] = int(n)
    else:
        c.mention_count = int(n)


def _set_char_aliases(c: Any, aliases: Sequence[str]) -> None:
    name = _char_name(c)
    cleaned = [a for a in aliases if a and a != name]
    if isinstance(c, dict):
        c["aliases"] = cleaned
    else:
        c.aliases = cleaned


def _set_char_importance(c: Any, imp: str) -> None:
    if isinstance(c, dict):
        c["importance"] = imp
    else:
        c.importance = imp


def _host_score(c: Any) -> tuple[int, int]:
    return (_char_mentions(c), len(_char_name(c)))


def _absorb_into(
    host: Any,
    victim: Any,
    *,
    absorbed: set[str],
    merged_log: list[dict[str, str]],
    reason: str,
) -> None:
    vname = _char_name(victim)
    hname = _char_name(host)
    if not vname or not hname or vname == hname or vname in absorbed:
        return
    _set_char_mentions(host, _char_mentions(host) + _char_mentions(victim))
    aliases = _char_aliases(host)
    for a in [vname, *_char_aliases(victim)]:
        if a and a != hname and a not in aliases:
            aliases.append(a)
    _set_char_aliases(host, aliases)
    absorbed.add(vname)
    merged_log.append({"from": vname, "into": hname, "reason": reason})


def name_edit_distance(a: str, b: str) -> int:
    """Levenshtein distance (small strings only)."""
    a = a or ""
    b = b or ""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    # Ensure a is shorter row for memory
    if len(a) > len(b):
        a, b = b, a
    prev = list(range(len(a) + 1))
    for j, bj in enumerate(b, start=1):
        cur = [j]
        for i, ai in enumerate(a, start=1):
            ins = cur[i - 1] + 1
            delete = prev[i] + 1
            sub = prev[i - 1] + (0 if ai == bj else 1)
            cur.append(min(ins, delete, sub))
        prev = cur
    return prev[-1]


def merge_alias_collisions(
    characters: Sequence[Any],
) -> tuple[list[Any], list[dict[str, str]]]:
    """Merge entries linked by alias into the stronger canonical.

    If A.name ∈ B.aliases or B.name ∈ A.aliases, keep the higher-mention
    (tie-break longer name) and fold the other in as an alias.
    """
    chars = [c for c in list(characters or []) if _char_name(c)]
    if len(chars) <= 1:
        return chars, []

    by_name: dict[str, Any] = {}
    for c in chars:
        n = _char_name(c)
        if n not in by_name or _char_mentions(c) > _char_mentions(by_name[n]):
            by_name[n] = c

    merged_log: list[dict[str, str]] = []
    absorbed: set[str] = set()

    names = list(by_name.keys())
    for name in names:
        if name in absorbed:
            continue
        left = by_name.get(name)
        if left is None:
            continue
        partners: list[Any] = []
        for other_name, other in by_name.items():
            if other_name == name or other_name in absorbed:
                continue
            if name in _char_aliases(other) or other_name in _char_aliases(left):
                partners.append(other)
        if not partners:
            continue
        partner = max(partners, key=_host_score)
        if _host_score(left) >= _host_score(partner):
            host, weak = left, partner
        else:
            host, weak = partner, left
        _absorb_into(host, weak, absorbed=absorbed, merged_log=merged_log, reason="alias")

    kept = [c for n, c in by_name.items() if n not in absorbed]
    kept.sort(key=lambda c: (-_char_mentions(c), _char_name(c)))
    return kept, merged_log


def merge_near_duplicates(
    characters: Sequence[Any],
    *,
    max_distance: int = 2,
    min_len: int = 4,
) -> tuple[list[Any], list[dict[str, str]]]:
    """Merge orthographic variants (维尔德拉 ↔ 维鲁多拉) by edit distance.

    Both names must be at least ``min_len``; distance ≤ ``max_distance``.
    Extra guard: same first character (cuts 利古路多↔利姆路 noise).
    Stronger mention_count wins as canonical.
    """
    chars = [c for c in list(characters or []) if _char_name(c)]
    if len(chars) <= 1:
        return chars, []

    max_d = max(0, int(max_distance))
    min_l = max(2, int(min_len))

    by_name: dict[str, Any] = {}
    for c in chars:
        n = _char_name(c)
        if n not in by_name or _char_mentions(c) > _char_mentions(by_name[n]):
            by_name[n] = c

    merged_log: list[dict[str, str]] = []
    absorbed: set[str] = set()
    names = sorted(
        by_name.keys(),
        key=lambda n: (-_char_mentions(by_name[n]), -len(n), n),
    )

    for i, a in enumerate(names):
        if a in absorbed or len(a) < min_l:
            continue
        for b in names[i + 1 :]:
            if b in absorbed or len(b) < min_l:
                continue
            if a[0] != b[0]:
                continue
            if abs(len(a) - len(b)) > max_d:
                continue
            if name_edit_distance(a, b) > max_d:
                continue
            # Shared-character floor: avoid loose matches like 利古路多↔利姆XX
            shared = len(set(a) & set(b))
            if shared < max(2, min(len(a), len(b)) - max_d):
                continue
            # Suffix-difference guard: if both names share a long common prefix
            # and the suffixes are entirely different → different characters with
            # the same surname (温水|佳树 ≠ 温水|和彦), not orthographic variants.
            prefix = 0
            for ca, cb in zip(a, b):
                if ca == cb:
                    prefix += 1
                else:
                    break
            suffix_a = a[prefix:]
            suffix_b = b[prefix:]
            if prefix >= 2 and len(suffix_a) >= 2 and len(suffix_b) >= 2:
                suffix_d = name_edit_distance(suffix_a, suffix_b)
                if suffix_d >= min(len(suffix_a), len(suffix_b)):
                    continue  # suffixes fully differ → different people
            ca, cb = by_name[a], by_name[b]
            if _host_score(ca) >= _host_score(cb):
                _absorb_into(
                    ca, cb, absorbed=absorbed, merged_log=merged_log, reason="near_dup"
                )
            else:
                _absorb_into(
                    cb, ca, absorbed=absorbed, merged_log=merged_log, reason="near_dup"
                )
                break  # ``a`` absorbed; stop pairing it

    kept = [c for n, c in by_name.items() if n not in absorbed]
    kept.sort(key=lambda c: (-_char_mentions(c), _char_name(c)))
    return kept, merged_log


def assign_importance_by_mentions(
    characters: Sequence[Any],
    *,
    main_top_n: int = 5,
    supporting_top_n: int = 20,
    blacklist: Sequence[str] | None = None,
    demote_to: str = "extra",
    speaker_scores: dict[str, int] | None = None,
) -> list[Any]:
    """Re-tier importance by mention_count + speaker activity.

    - Blacklisted names (exact) → ``demote_to`` (default extra), excluded from TopN
    - Top ``main_top_n`` eligible by activity score → main
    - Next ``supporting_top_n`` → supporting
    - Remainder → extra

    ``speaker_scores``（可选，名字 → 说话活跃章数）修正"被提及多但不开口"
    的角色：真正有台词的角色（主角/常驻角色）应得到 main/supporting 配额。
    Mutates InventoryCharacter / dicts in place; returns ranked list (eligible first).
    """
    main_n = max(0, int(main_top_n))
    supp_n = max(0, int(supporting_top_n))
    bl = {str(x).strip() for x in (blacklist or []) if str(x).strip()}
    demote = demote_to if demote_to in ("main", "supporting", "extra") else "extra"

    chars = list(characters or [])
    blocked: list[Any] = []
    eligible: list[Any] = []
    for c in chars:
        if _char_name(c) in bl:
            _set_char_importance(c, demote)
            blocked.append(c)
        else:
            eligible.append(c)

    ranked = sorted(
        eligible,
        key=lambda c: _active_score(c, speaker_scores),
        reverse=True,
    )
    for i, c in enumerate(ranked):
        if i < main_n:
            _set_char_importance(c, "main")
        elif i < main_n + supp_n:
            _set_char_importance(c, "supporting")
        else:
            _set_char_importance(c, "extra")

    blocked_sorted = sorted(blocked, key=_char_mentions, reverse=True)
    return ranked + blocked_sorted


