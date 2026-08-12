"""DeepSeek unified character + QA extraction — single-call replacement for per-character Phase 3d.

Prompt uses hardcoded dimension names to prevent LLM drift (R2 finding).
"""
from __future__ import annotations

import json
import logging
import re
import time

logger = logging.getLogger("agent")

# ── Hardcoded dimension names (R2 fix: prevent LLM from renaming) ──

UNIFIED_SYSTEM_PROMPT = """你是日轻小说角色分析专家。分析以下小说的所有角色，输出每个角色的完整档案。

## 输出格式（严格遵守字段名，不要修改）

以角色名为 key 输出 JSON 对象：
{
  "角色A": {
    "traits": {
      "extraversion": 数值0.0-1.0,
      "agreeableness": 数值0.0-1.0,
      "conscientiousness": 数值0.0-1.0,
      "neuroticism_reverse": 数值0.0-1.0,
      "dominance": 数值0.0-1.0,
      "complexity": 数值0.0-1.0
    },
    "speech": {
      "vocabulary": "字符串：用词特点",
      "sentence_pattern": "字符串：句式特点",
      "catchphrase": "字符串：口头禅",
      "emotional_expression": "字符串：情绪表达方式",
      "rhythm": "字符串：语调节奏"
    },
    "catchphrases": ["口头禅1", "口头禅2"],
    "emotional_tendencies": "字符串：主要情绪倾向",
    "role": "字符串：角色定位"
  },
  ...
}

## 重要
- traits 必须使用上述 6 个字段名，不要改名
- speech 必须使用上述 5 个字段名，不要改名
- 不要添加额外字段（如 openness, assertiveness, formality, politeness 等）
- 只输出 JSON，不要任何其他文字"""

_UNIFIED_USER_TEMPLATE = """小说：{title}

以下是从小说中提取的角色及其对话和叙事片段。请分析所有角色的性格档案。

{character_sections}

请输出所有角色的完整档案 JSON。"""


def build_unified_prompt(
    title: str,
    characters: dict[str, dict],  # {name: {"dialogues": [...], "narratives": [...]}}
    narratives: dict[str, list[str]] | None = None,
) -> str:
    """构建统一角色提取 prompt。

    Args:
        title: 书名。
        characters: 角色数据，每个角色包含 dialogues 和可选的 narratives。
        narratives: 可选，叙事上下文 {name: [context_str]}。

    Returns:
        完整 prompt 字符串。
    """
    sections = []
    for name, info in characters.items():
        dialogues = "\n".join(f"    - {d}" for d in info.get("dialogues", [])[:8])
        narr = ""
        if narratives and name in narratives:
            narr = "\n".join(f"    {n}" for n in narratives[name][:5])
            narr = f"\n  叙事上下文：\n{narr}"

        count = info.get("count", len(info.get("dialogues", [])))
        sections.append(
            f"""角色：{name}（{count} 次对话）{narr}
  对话采样：
{dialogues}"""
        )

    return _UNIFIED_USER_TEMPLATE.format(
        title=title,
        character_sections="\n\n".join(sections),
    )


async def extract_characters_unified(
    llm_client,
    title: str,
    characters: dict[str, dict],
    narratives: dict[str, list[str]] | None = None,
) -> dict[str, dict]:
    """一次 DeepSeek 调用提取所有角色档案。

    Args:
        llm_client: SharedLLMClient 实例。
        title: 书名。
        characters: 角色数据 {name: {dialogues: [...], count: N}}。
        narratives: 可选叙事上下文。

    Returns:
        {name: {traits, speech, catchphrases, emotional_tendencies, role}}。
    """
    user_prompt = build_unified_prompt(title, characters, narratives)

    t0 = time.perf_counter()
    try:
        raw = await llm_client.achat(
            messages=[
                {"role": "system", "content": UNIFIED_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=8192,
            extra_body={"thinking": {"type": "disabled"}},
        )
    except Exception as e:
        logger.warning("Unified character extraction failed: %s", e)
        return {}

    elapsed = time.perf_counter() - t0
    logger.info("Unified extraction: %d chars → %d chars output, %.1fs",
                 len(user_prompt), len(raw), elapsed)

    # Parse JSON — multi-strategy with logging
    data = None
    raw_preview = raw[:200] if raw else "(empty)"

    # Strategy 1: direct parse
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Strategy 2: code fence — try lazy then greedy
    if data is None:
        for fence_pat in [
            r"```(?:json)?\s*\n?(\{.*\})\n?```",   # greedy
            r"```(?:json)?\s*\n?(\[.*\])\n?```",    # array
            r"```(?:json)?\s*\n?(.*?)\n?```",        # lazy fallback
        ]:
            m = re.search(fence_pat, raw, re.DOTALL)
            if m:
                try:
                    data = json.loads(m.group(1))
                    break
                except json.JSONDecodeError:
                    continue

    # Strategy 3: find outermost { }
    if data is None:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(raw[start:end+1])
            except json.JSONDecodeError:
                pass

    if data is None:
        logger.warning(
            "Unified extraction: JSON parse failed. raw[:300]=%s",
            raw_preview,
        )
        return {}

    if not isinstance(data, dict):
        return {}

    # Validate: each value should be a dict with traits
    result = {}
    for name, profile in data.items():
        if isinstance(profile, dict) and "traits" in profile:
            result[name] = profile
        else:
            logger.debug("Unified extraction: skipping invalid entry for '%s'", name)

    return result
