"""LLM 后处理判断 + 判断回收（verdict 解析与上报）。

设计（docs/WORLD_KNOWLEDGE_TOOL_DESIGN.md v5 §2）：
- LLM 后处理判断照做：召回原文相关性、工具结果价值、可答性
- 判断结果回收为 Prometheus 指标（metrics.observe_*），在线评估"数据相关性
  是否足够解决用户问题"
- 实现方式（决策 6）：判断并入主 LLM 回答——prompt 末尾要求 LLM 在回复后附
  一行隐藏 JSON verdict，解析后剥离并上报；不额外调 LLM

verdict 行格式（放在回复最末尾，独立一行，前缀固定）：
    <<VERDICT>>{"relevant": 3, "irrelevant": 1, "tool_used": true, "tool_query_type": "relations", "tool_valuable": true, "answerable": true}<<END>>
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger("agent")

_VERDICT_RE = re.compile(
    r"<<VERDICT>>(\{.*?\})<<END>>",
    re.DOTALL,
)

_VERDICT_INSTRUCTION = (
    "\n\n回复结束后，另起一行输出（不要输出其他内容，供系统统计用）：\n"
    "<<VERDICT>>{\"relevant\": <你实际采用的相关原文片段数>, "
    "\"irrelevant\": <你忽略的无关片段数>, "
    "\"tool_used\": true/false, "
    "\"tool_query_type\": \"relations/events/timeline/lorebook/character_events/null\", "
    "\"tool_valuable\": true/false, "
    "\"answerable\": true/false}<<END>>\n"
    "（relevant+irrelevant 应等于你收到的检索片段总数；没有检索到片段时为 0。"
    "tool_used 表示是否采用了工具查询结果。answerable 表示现有信息能否回答用户问题。）"
)


def verdict_instruction() -> str:
    """附加到 LLM 消息末尾的 verdict 指令。"""
    return _VERDICT_INSTRUCTION


def parse_verdict(reply: str) -> tuple[str, dict[str, Any] | None]:
    """从回复中提取 verdict JSON 并剥离。

    返回 (clean_reply, verdict_dict | None)。verdict 解析失败返回 (原回复, None)。
    """
    if not reply:
        return reply, None
    m = _VERDICT_RE.search(reply)
    if not m:
        return reply, None
    try:
        verdict = json.loads(m.group(1))
    except (json.JSONDecodeError, TypeError):
        return reply, None
    clean = reply[: m.start()].rstrip()
    return clean, verdict


def report_verdict(verdict: dict[str, Any] | None) -> None:
    """把 verdict 回收为 Prometheus 指标。verdict 为 None 或缺失字段时静默跳过。"""
    if not verdict:
        return
    try:
        from src.shared.metrics import (
            observe_answer_coverage,
            observe_retrieval_relevance,
            observe_tool_value,
        )

        relevant = int(verdict.get("relevant") or 0)
        irrelevant = int(verdict.get("irrelevant") or 0)
        for _ in range(max(0, relevant)):
            observe_retrieval_relevance(verdict="relevant")
        for _ in range(max(0, irrelevant)):
            observe_retrieval_relevance(verdict="irrelevant")

        tool_used = bool(verdict.get("tool_used"))
        if tool_used:
            qt = str(verdict.get("tool_query_type") or "unknown")
            valuable = bool(verdict.get("tool_valuable"))
            observe_tool_value(
                query_type=qt,
                verdict="valuable" if valuable else "useless",
            )

        answerable = bool(verdict.get("answerable"))
        observe_answer_coverage(verdict="answerable" if answerable else "unanswerable")
    except Exception as exc:  # noqa: BLE001 - 回收失败不影响主链路
        logger.debug("Verdict report failed: %s", exc)
