"""LLM 别名归并结果的等价校验（设计: docs/ALIAS_UNIFICATION_DESIGN.md B 层）。

validate_merge_groups 保证一对一等价：
- E1 任一变体只能属于一个 canonical（防"温水"同时映射两人）
- E2 canonical 不重复
- E3 canonical 非空且 ≥2 字
- W2 组内有更长变体 → canonical 可能截断（如 坦派斯 vs 坦派斯特）
"""

from __future__ import annotations

from typing import Any

# 称号前缀：变体 = 称号+本名（魔王盖德 → 盖德）时 canonical 较短是正常的，不算截断
_HONORIFIC_PREFIXES = (
    "魔王", "大人", "同学", "小姐", "先生", "学姐", "学长", "老师", "部长",
    "前辈", "殿下", "大人", "君", "酱", "ちゃん", "さん", "阁下",
)


def _strip_honorific_prefix(v: str) -> str:
    for h in _HONORIFIC_PREFIXES:
        if v.startswith(h) and len(v) > len(h):
            return v[len(h):]
    return v


def merge_duplicate_canonicals(
    groups: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """跨批后处理：相同 canonical 的组合并（variants 并集去重）。

    分批归并时同一角色可能在不同批被重复归并（E2 canonical 重复）——
    先合并再校验，消除批间重复。reason 取首个非空。
    """
    merged: dict[str, dict[str, Any]] = {}
    for g in groups:
        canon = str(g.get("canonical") or "").strip()
        if not canon:
            continue
        if canon in merged:
            prev = merged[canon]
            prev_variants = list(prev.get("variants") or [])
            cur_variants = list(g.get("variants") or [])
            prev["variants"] = list(dict.fromkeys(prev_variants + cur_variants))
            if not prev.get("reason"):
                prev["reason"] = g.get("reason")
        else:
            merged[canon] = dict(g)
    return list(merged.values())


def validate_merge_groups(groups: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    """一对一等价校验。返回 (errors, warnings)；errors 非空则不得落盘。"""
    errors: list[str] = []
    warnings: list[str] = []
    variant_owner: dict[str, str] = {}
    canonical_set: set[str] = set()

    for gi, g in enumerate(groups):
        canon = str(g.get("canonical") or "").strip()
        variants = [str(v).strip() for v in (g.get("variants") or []) if str(v).strip()]

        if not canon or len(canon) < 2:
            errors.append(f"E3 组{gi}: canonical 为空或过短: {canon!r}")
            continue
        if canon in canonical_set:
            errors.append(f"E2 组{gi}: canonical 重复: {canon}")
        canonical_set.add(canon)

        for v in variants:
            if not v or len(v) < 2:
                continue
            prev = variant_owner.get(v)
            if prev is not None:
                errors.append(f"E1 变体冲突: {v!r} 同时属于 {prev!r} 和 {canon!r}")
            else:
                variant_owner[v] = canon

        # W2: 更长变体且「非称号+本名」形态 → canonical 可能截断（凯金正 → 凯金）
        # 魔王盖德(称号) 剥离后=盖德 不警告；凯金正 无称号前缀 → 警告
        longer = [
            v for v in variants
            if len(v) > len(canon) and _strip_honorific_prefix(v) == v
        ]
        if longer:
            warnings.append(f"W2 组{canon}: 组内有更长变体 {longer}，canonical 可能截断")

    return errors, warnings
