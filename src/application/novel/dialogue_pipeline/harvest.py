"""LLM-based chapter name harvest for dialogue attribution candidates.

Replaces regex ``candidates_from_text`` as the primary per-chapter harvest
path (design: docs/LLM_HARVEST_CHARACTER_NAMES_DESIGN.md). The regex path
stays as a silent fallback when the LLM is unavailable or fails.

Contract:
    harvest_chapter_names(...) -> list[str] | None
      - list[str]  : names that passed the in-text hallucination guard
      - []         : harvest succeeded but no usable names
      - None       : LLM unavailable / call failed / bad output -> caller
                     falls back to the regex path
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger("agent")

# 职业/占位词表：harvest 输出中出现的职业词/占位符不是角色名
# 已收敛至 src/domain/novel/character_policy.py（ROLE_WORDS）
from src.domain.novel.character_policy import ROLE_WORDS as _OCCUPATION_NOISE

_OCCUPATION_NOISE = set(_OCCUPATION_NOISE)

_HARVEST_PROMPT = """你是小说对话归因助手。从下面的章节文本中，提取【说话人】名字清单。

规则：
- 只提取实际说了话的人（引号台词旁或叙事句中的说话者）
- 名字可以是任意长度（中文名、翻译名）；敬称变体归一为纯名（"利姆露大人"→"利姆露"）
- 被呼叫方不算说话人（"维鲁多拉大人，快醒醒"的说话人是呼叫者，不是维鲁多拉）
- 忽略：他/她/众人/少年 等指代词、旁白、无名字的碎片
- 不确定的名字不要编造；最多 {max_names} 个

章节文本：
{text}

输出 JSON：{{"names": ["八奈见杏菜", "烧盐", ...]}}"""


def _extract_json(raw: str) -> str | None:
    """Three-layer tolerant JSON extraction: fence -> pure JSON -> braces slice."""
    text = (raw or "").strip()
    if not text:
        return None
    # 1) markdown code fence
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    # 2) pure JSON object
    if text.startswith("{"):
        return text
    # 3) first { to last }
    a, b = text.find("{"), text.rfind("}")
    if a != -1 and b > a:
        return text[a : b + 1]
    return None


def _parse_names(raw: str) -> list[str] | None:
    """Parse LLM output into a name list; None when shape is unusable."""
    body = _extract_json(raw)
    if body is None:
        return None
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        # tolerate trailing commas before closing brace
        try:
            data = json.loads(re.sub(r",\s*([}\]])", r"\1", body))
        except json.JSONDecodeError:
            return None
    names = data.get("names") if isinstance(data, dict) else None
    if not isinstance(names, list):
        return None
    out: list[str] = []
    for n in names:
        s = str(n or "").strip()
        if s and s not in out:
            out.append(s)
    return out


def _filter_names(
    names: list[str],
    chapter_text: str,
    *,
    max_names: int,
) -> list[str]:
    """Hallucination guard: every name must appear in the chapter text."""
    from src.domain.novel.dialogue_span import is_noise_speaker

    text = chapter_text or ""
    out: list[str] = []
    for n in names:
        if len(n) < 2:
            continue
        if is_noise_speaker(n):
            continue
        if n in _OCCUPATION_NOISE:
            logger.debug("harvest drop (occupation/placeholder): %s", n)
            continue
        if n not in text:
            logger.debug("harvest drop (not in text): %s", n)
            continue
        if n not in out:
            out.append(n)
        if len(out) >= max_names:
            break
    return out


async def harvest_chapter_names(
    chapter_text: str,
    llm_client: Any,
    *,
    max_names: int = 20,
    max_tokens: int = 512,
) -> list[str] | None:
    """Harvest speaker names from one chapter via LLM.

    Returns None on any failure (caller silently falls back to the regex
    harvest path); [] when the chapter has no usable names.
    """
    if llm_client is None:
        return None
    text = (chapter_text or "").strip()
    if not text:
        return None
    prompt = _HARVEST_PROMPT.format(text=text[:8000], max_names=max_names)
    try:
        raw = await llm_client.achat(
            [{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=max_tokens,
        )
    except Exception as e:  # noqa: BLE001 - harvest is best-effort
        logger.warning("chapter harvest LLM call failed: %s", e)
        return None
    if not raw or not str(raw).strip():
        return None
    parsed = _parse_names(str(raw))
    if parsed is None:
        logger.warning("chapter harvest unparsable output: %.200s", str(raw)[:200])
        return None
    return _filter_names(parsed, text, max_names=max_names)
