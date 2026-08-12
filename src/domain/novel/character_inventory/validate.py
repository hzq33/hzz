"""方案 I 校验层 — LLM 语义校验（单次调用，无修正循环）。

旧模式（validate_and_retry）：硬编码规则列表 → 发现错误 → LLM 修正 → 再校验 → 循环 ≤2 轮。
反模式：规则列表永远不全（作家/身份词枚举不完），循环放大了 LLM 调用成本。

新模式（validate_by_llm）：
  normalize (LLM 一次产出)
    → 规则快速预检（validate，零成本；通过则跳过 LLM 校验）
    → LLM 校验（单次调用：角色表 + R1/R2/R3 规则 → 语义判断 + 直接输出修正后完整 JSON）
    → 规则最终复查（validate，仅记录 warning 不触发循环）

LLM 语义校验优于硬编码名单：能识别"拿破仑"是名人但"利姆露"不是，
泛化覆盖硬编码列表之外的作家/名人/身份词。
"""

from __future__ import annotations

import json, logging, re
from typing import Any

# 作家表 / 身份词已收敛至 character_policy 单一事实源（含"太宰"等碎片）
from src.domain.novel.character_policy import (
    KNOWN_WRITERS,
    ROLE_WORDS,
)

logger = logging.getLogger("agent")

# 兼容别名（旧代码/测试引用）
_KNOWN_WRITERS = KNOWN_WRITERS
_ROLE_WORDS = ROLE_WORDS


def validate(characters: list[dict], series_hint: str = "") -> list[str]:
    """返回错误列表。空列表 = 通过。"""
    errors: list[str] = []

    canons = {c.get("canonical_name", "").strip() for c in characters if c.get("canonical_name")}

    # R1: 别名唯一性 — 同一 alias 不能属于两个 canonical
    alias_to_owners: dict[str, set[str]] = {}
    for c in characters:
        canon = str(c.get("canonical_name", "")).strip()
        if not canon:
            continue
        for a in (c.get("aliases") or []):
            a = str(a).strip()
            if a and a != canon:
                alias_to_owners.setdefault(a, set()).add(canon)
    for alias, owners in alias_to_owners.items():
        if len(owners) > 1:
            errors.append(
                f"别名冲突: {alias} 同时属于 {sorted(owners)}。"
                f"请决定 {alias} 属于谁，从另一角色的 aliases 中移除。"
            )

    # R2/R3: per-character 检查
    for i, c in enumerate(characters):
        canon = str(c.get("canonical_name", "")).strip()
        aliases = [str(a).strip() for a in (c.get("aliases") or []) if str(a).strip()]

        # R2: 作家/名人名在角色表中
        if canon in _KNOWN_WRITERS:
            errors.append(
                f"作家/名人: #{i} canonical={canon} 是作家或名人，不应出现在角色表。"
                f"请将其移至 dropped。"
            )
        for alias in aliases:
            if alias in _KNOWN_WRITERS:
                errors.append(
                    f"作家/名人: #{i} canonical={canon} aliases 包含作家名 {alias}。"
                )

        # R3: 身份词作为 canonical
        if canon in _ROLE_WORDS:
            errors.append(
                f"身份词: #{i} canonical={canon} 是身份词而非角色名。"
                f"请将其移至 dropped 或给出完整角色名。"
            )

    return errors[:10]  # 最多 10 条


_LLM_VALIDATION_PROMPT = """你是轻小说角色表校验器。检查角色表是否违反以下规则，如有违反，直接输出修正后的完整 JSON。

规则：
R1. 别名唯一性 — 同一个 alias 不能同时属于两个不同角色（canonical_name）。
R2. 作家/名人去噪 — 文中引用的作家/历史名人（如太宰治、夏目漱石、拿破仑等）不能出现在角色表（characters）中，应移入 dropped。
R3. 身份词去噪 — 职业/身份词（部长、店员、老师、学姐、会长等）不能作为 canonical_name，除非它是该作品中的唯一指代（如某角色真名就叫"老师"且全文只用此称呼）。

作品提示：{series_hint}

要求：
- 如果角色表完全合规，原样输出（不要改动）。
- 如果有违反，修正后输出完整 JSON。
- 不要新增证据中不存在的角色，不要编造名字。
- 只输出 JSON：
{{"characters":[{{"canonical_name":"...","aliases":["..."],"importance":"main|supporting|extra","from_clusters":["c1"]}}],"dropped":[{{"from_clusters":["c9"],"reason":"..."}}]}}

当前角色表：
{payload}
"""


async def validate_by_llm(
    llm_client: Any,
    characters: list[dict],
    dropped: list[dict],
    series_hint: str = "",
) -> tuple[list[dict], list[dict]]:
    """LLM 校验层：规则预检 → 单次 LLM 语义校验修正（无循环）→ 规则复查。

    Returns:
        (characters, dropped) — 修正后（或原样）的角色表。

    Deprecated: 新链路使用 ``resolve_violations``（确定性裁决，零 LLM 成本）。
    本函数保留供旧调用方/测试兼容。
    """
    # 规则快速预检：干净则零成本跳过 LLM 校验
    errors = validate(characters, series_hint)
    if not errors:
        logger.debug("Validation passed (rule pre-check, LLM not needed)")
        return characters, dropped
    logger.info(
        "LLM validation triggered: %d rule violations (R1 alias/R2 writer/R3 role word)",
        len(errors),
    )

    payload = json.dumps(
        {"characters": characters, "dropped": dropped},
        ensure_ascii=False,
        indent=2,
    )
    prompt = _LLM_VALIDATION_PROMPT.format(
        series_hint=series_hint or "（未指定）",
        payload=payload,
    )

    try:
        raw = await llm_client.achat(
            [{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=4096,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM validation call failed (%s); keeping original table", exc)
        return characters, dropped

    raw = raw or ""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        try:
            data = json.loads(m.group()) if m else {}
        except json.JSONDecodeError:
            data = {}
    if not data:
        logger.warning("LLM validation returned unparseable output; keeping original table")
        return characters, dropped

    fixed_chars = list(data.get("characters", []) or characters)
    fixed_dropped = list(data.get("dropped", []) or dropped)

    # 规则最终复查（最终防线，仅记录不循环；宁可落盘 + 人工复核）
    final_errors = validate(fixed_chars, series_hint)
    if final_errors:
        logger.warning(
            "LLM validation still has %d rule violations — persisting for manual review: %s",
            len(final_errors),
            final_errors[:3],
        )
    else:
        logger.info("LLM validation fixed all %d violations", len(errors))
    return fixed_chars, fixed_dropped


def resolve_violations(
    characters: list[dict],
    dropped: list[dict],
    series_hint: str = "",
) -> tuple[list[dict], list[dict], list[str]]:
    """确定性裁决：不调 LLM，用规则直接修正角色表（零成本、可复现）。

    规则：
      R2 作家/名人 canonical → 移入 dropped；aliases 含作家名 → 移除
      R3 身份词 canonical → 移入 dropped
      R1 别名冲突（alias 属多个 canonical）→ 保留 mention_count 最高者，
         其余 owner 移除该 alias

    Returns:
        (fixed_characters, fixed_dropped, remaining_errors)
        remaining_errors 非空表示裁决后仍有无法自动解决的违规——
        调用方应拒绝落盘（回退 NER 簇）而非污染数据。
    """
    chars: list[dict] = [dict(c) for c in characters]
    drops: list[dict] = [dict(d) for d in dropped]

    # ── R2/R3：作家/身份词 canonical 移入 dropped，alias 中的作家名移除 ──
    kept: list[dict] = []
    for c in chars:
        canon = str(c.get("canonical_name") or "").strip()
        if not canon:
            continue
        if canon in _KNOWN_WRITERS:
            drops.append({
                "from_clusters": list(c.get("from_clusters") or []),
                "reason": f"作家/名人: {canon}",
            })
            continue
        if canon in _ROLE_WORDS:
            drops.append({
                "from_clusters": list(c.get("from_clusters") or []),
                "reason": f"身份词: {canon}",
            })
            continue
        aliases = [
            str(a).strip()
            for a in (c.get("aliases") or [])
            if str(a).strip() and str(a).strip() not in _KNOWN_WRITERS
        ]
        c["aliases"] = aliases
        kept.append(c)
    chars = kept

    # ── R1：别名冲突 → 保留 mention 最高者 ──
    alias_owners: dict[str, list[int]] = {}
    for i, c in enumerate(chars):
        canon = str(c.get("canonical_name") or "").strip()
        for a in (c.get("aliases") or []):
            a = str(a).strip()
            if a and a != canon:
                alias_owners.setdefault(a, []).append(i)
    for alias, owners in alias_owners.items():
        if len(owners) <= 1:
            continue
        best = max(
            owners,
            key=lambda i: (int(chars[i].get("mention_count") or 0), -i),
        )
        for i in owners:
            if i == best:
                continue
            c = dict(chars[i])
            c["aliases"] = [
                str(x).strip()
                for x in (c.get("aliases") or [])
                if str(x).strip() != alias
            ]
            chars[i] = c

    remaining = validate(chars, series_hint)
    if remaining:
        logger.warning(
            "resolve_violations left %d unresolved violations: %s",
            len(remaining),
            remaining[:3],
        )
    return chars, drops, remaining
