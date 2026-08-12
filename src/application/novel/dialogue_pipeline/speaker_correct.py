"""说话人 LLM 校正 — 把对话抽取产出的说话人碎片/简称归一到权威角色名。

背景：dialogue_attribution 对生僻名/日语原名的归因偶发产出截断碎片
（如「微微一」「佳树皮」「奈见别」）或只用简称（「温水」「志喜屋」）。
这些碎片若直接进 roster/扮演检索会污染质量。

本模块在对话块生成后、入库前做一次 LLM 校正：
- 输入：全量说话人 + 权威角色名（来自 inventory / 卷内高频名）
- 输出：raw → target 映射（target ∈ {权威名, "noise", 原样}）
- 应用：替换对话块 speaker；noise 标记为「未知」并由上层按 is_noise 处理

设计：
- 一次 LLM 调用处理全部说话人（批量，成本可控）
- 失败静默降级（保留原样，不阻塞入库）
- 配置开关：dialogue_attribution.llm_correct_speakers（默认 true）
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger("agent")

_PROMPT = """你是小说角色名校正器。下面是从小说对话中抽出的说话人名单，有些是正确角色名，有些是截断/拼错的碎片，有些是简称。

## 权威角色名（全集，同名或别名归一到这些名）
{authority}

## 待校正的说话人名单
{speakers}

逐条判断每个说话人：
1. 与权威名相同，或是其别名/简称/称呼（如 温水→温水和彦、志喜屋→志喜屋梦子、月之木学姐→月之木古都、玉木部长→玉木慎太郎、甘夏老师→甘夏古奈美）→ target=该权威名
2. 明显是碎片/无意义（截断名、助词组合、通用占位，如 微微一、佳树皮、会被人、女生A、店员）→ target="noise"
3. 无法归类但像真实人名（不在权威名单但可信）→ target=原样（保留）

只输出 JSON 数组（不要其他文字）：
[{{"raw":"碎片名","target":"权威名或noise或原样","reason":"一句话理由"}}]
"""


def _extract_json(text: str) -> list[dict]:
    if not text:
        return []
    m = re.search(r"\[[\s\S]*\]", text or "")
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
        return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


async def correct_speakers(
    speakers: list[str],
    authority: list[str],
    llm_client: Any,
    *,
    max_names: int = 200,
) -> dict[str, str]:
    """Return {raw_speaker: target}. target ∈ {权威名, "noise", 原样}。

    调用失败返回空 dict（调用方降级保留原样）。
    """
    unique = list(dict.fromkeys(s for s in speakers if s and s.strip()))[:max_names]
    if not unique or not authority or llm_client is None:
        return {}
    prompt = _PROMPT.format(
        authority="、".join(authority[:80]),
        speakers="、".join(unique),
    )
    try:
        raw = await llm_client.achat(
            [{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=4096,
        )
    except Exception as exc:  # noqa: BLE001 - 校正失败不影响入库
        logger.warning("Speaker correction LLM failed: %s", exc)
        return {}
    items = _extract_json(raw or "")
    if not items:
        logger.warning("Speaker correction returned no usable output")
        return {}
    mapping: dict[str, str] = {}
    for item in items:
        r = str(item.get("raw") or "").strip()
        t = str(item.get("target") or "").strip()
        if r and t:
            mapping[r] = t
    kept = sum(1 for v in mapping.values() if v != "noise")
    logger.info(
        "Speaker correction: %d mapped (%d kept, %d noise)",
        len(mapping), kept, len(mapping) - kept,
    )
    return mapping


def apply_speaker_mapping(
    blocks: list,
    mapping: dict[str, str],
    *,
    is_noise_speaker=None,
) -> tuple[int, int]:
    """Apply mapping to dialogue blocks in place.

    Returns (changed, noise_marked). ``noise`` targets are set to "未知" so
    the caller's normal noise filtering drops them.
    """
    if not mapping:
        return 0, 0
    if is_noise_speaker is None:
        try:
            from src.domain.novel.dialogue_span import is_noise_speaker as _n
            is_noise_speaker = _n
        except Exception:  # noqa: BLE001
            is_noise_speaker = None
    changed = 0
    noise_marked = 0
    for block in blocks:
        for turn in getattr(block, "dialogues", None) or []:
            sp = (getattr(turn, "speaker", None) or "").strip()
            target = mapping.get(sp)
            if target is None:
                continue
            if target == "noise":
                turn.speaker = "未知"
                noise_marked += 1
            elif target and target != sp:
                turn.speaker = target
            changed += 1
    return changed, noise_marked
