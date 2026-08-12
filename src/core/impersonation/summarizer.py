"""上下文压缩摘要器 — 角色扮演早期轮次压缩为结构化摘要。

触发时机：每轮 chat 结束后由 ``ImpersonationAgent`` 调用 ``maybe_compact``；
当 memory 估算 token 超过 ``max_tokens * threshold`` 时：
1. 取最旧若干 user/assistant 轮次（保留最近 keep_turns 轮完整对话）
2. 调 LLM 生成结构化摘要（facts / open_questions / narrative）
3. 摘要写入 memory.summary，移除被压缩的消息

设计要点：
- 摘要面向"防遗忘/防跨轮矛盾"：facts 记录已确认事实与承诺，角色每轮回复都
  能看到（由 chat.py 注入 system 块），避免检索漏召回时凭记忆说错。
- 独立 LLM 调用（temperature 0.2），失败静默降级——压缩失败不影响主链路，
  仅本次不压缩，下轮再试。
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("agent")

_SUMMARIZE_PROMPT = """你是角色扮演会话的**上下文压缩器**。把一段较早的对话压缩成结构化摘要，让角色在后续对话中不遗忘关键事实、不出现前后矛盾。

当前角色：{character}

## 待压缩的早期对话
{dialogue}

## 已有摘要（如无则忽略，需与新内容合并）
{existing_summary}

请输出 JSON（不要输出其他内容）：
{{
  "narrative": "这段对话的脉络，一句话概括（不重复具体事实）",
  "facts": [
    "已确认的事实/关系/承诺，每条一句话，保留原话关键信息"
  ],
  "open_questions": [
    "尚未解决/角色承诺后续要做的事，无则空数组"
  ]
}}

要求：
1. facts 只写对话中**明确出现**的内容，不推测、不补充角色设定之外的信息。
2. 已有摘要中的 facts 若仍有效，必须保留（合并去重）。
3. 与角色人设、原著无关的寒暄（"在吗""你好"）不收入 facts。
4. JSON 必须合法，字符串用双引号。"""


def _count_turns(messages: list[dict[str, str]]) -> int:
    """Count user turns (one user message = one dialogue turn)."""
    return sum(1 for m in messages if m.get("role") == "user")


def _format_dialogue(messages: list[dict[str, str]], character: str) -> str:
    lines: list[str] = []
    for m in messages:
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        label = "用户" if role == "user" else character
        lines.append(f"{label}: {str(m.get('content') or '').strip()}")
    return "\n".join(lines)


def _parse_summary_json(text: str) -> dict[str, Any] | None:
    """Robust JSON extraction: strip code fences / surrounding prose."""
    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        # Remove ```json ... ``` fence
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    # Find first { ... last }
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return data


def _format_summary(data: dict[str, Any]) -> str:
    """Serialize parsed summary into the compact text stored in memory."""
    narrative = str(data.get("narrative") or "").strip()
    facts = data.get("facts") or []
    open_questions = data.get("open_questions") or []
    if not isinstance(facts, list):
        facts = []
    if not isinstance(open_questions, list):
        open_questions = []
    parts: list[str] = []
    if narrative:
        parts.append(f"脉络：{narrative}")
    if facts:
        parts.append("已确认：" + "；".join(str(f).strip() for f in facts if str(f).strip()))
    if open_questions:
        parts.append(
            "待办/未决：" + "；".join(str(q).strip() for q in open_questions if str(q).strip())
        )
    return "\n".join(parts)


async def summarize_dialogue(
    llm,
    *,
    character: str,
    messages: list[dict[str, str]],
    existing_summary: str = "",
    max_tokens: int = 500,
) -> str:
    """Compress old dialogue turns into a structured summary string.

    Returns the compact summary text; empty string on failure (caller keeps
    the old messages and retries next turn).
    """
    if not messages:
        return ""
    dialogue = _format_dialogue(messages, character)
    if not dialogue.strip():
        return ""
    prompt = _SUMMARIZE_PROMPT.format(
        character=character,
        dialogue=dialogue[:6000],
        existing_summary=existing_summary or "（无）",
    )
    try:
        reply = await llm.achat(
            [{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=max_tokens,
        )
    except Exception as exc:  # noqa: BLE001 - 压缩失败不影响主链路
        logger.warning("Context summarization LLM failed: %s", exc)
        return ""
    data = _parse_summary_json(reply or "")
    if data is None:
        logger.warning("Context summarization returned unparseable output; skipped")
        return ""
    return _format_summary(data)
