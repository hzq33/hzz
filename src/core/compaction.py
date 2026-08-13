"""Shared context-compaction routine for general chat and impersonation.

先摘要成功再删除（peek-then-commit）：摘要失败/为空时消息原封不动，
从根本上消除旧的"先删后补"模式里失败分支漏回滚导致的上下文丢失。
"""

from __future__ import annotations

import logging

from src.core.memory import ConversationMemory

logger = logging.getLogger("agent")


async def compact_memory(
    *,
    mem: ConversationMemory,
    llm,
    character: str,
    summarize_threshold: float,
    keep_turns: int,
) -> bool:
    """Fold the oldest turns into a summary, never losing messages on failure.

    Returns True when compaction (or a hard-trim fallback) happened.
    """
    threshold_tokens = max(100, int(mem.max_tokens * summarize_threshold))
    if mem.estimate_tokens() <= threshold_tokens:
        return False

    turn_msgs = [
        m for m in mem.get_messages() if m.get("role") in ("user", "assistant")
    ]
    keep_count = keep_turns * 2
    if len(turn_msgs) <= keep_count + 2:
        return False

    # 先窥视（不删除）将被压缩的消息
    removed = mem.peek_oldest(keep=keep_count)
    if not removed:
        return False
    removed_turns = sum(1 for m in removed if m.get("role") == "user")

    summary = ""
    try:
        from src.core.impersonation.summarizer import summarize_dialogue

        summary = await summarize_dialogue(
            llm,
            character=character,
            messages=removed,
            existing_summary=mem.get_summary(),
        )
    except Exception as exc:  # noqa: BLE001 - 压缩失败不影响主链路
        logger.warning("Context compaction failed: %s", exc)

    if not summary:
        # 摘要失败/为空：消息原封不动（peek 未删除）。
        # 仅当上下文已严重超限时，为避免无界膨胀才强制硬裁剪。
        hard_limit = max(2000, mem.max_tokens * 4)
        if mem.estimate_tokens() > hard_limit:
            logger.warning(
                "Context compaction failing repeatedly; hard-trim to %d tokens",
                mem.max_tokens,
            )
            mem.drop_oldest(keep=keep_count)
            mem.add_summarized_turns(removed_turns)
            return True
        return False

    # 摘要成功 → 才真正删除并记录
    mem.drop_oldest(keep=keep_count)
    prev = mem.get_summary()
    merged = f"{prev}\n{summary}".strip() if prev else summary
    mem.set_summary(merged)
    mem.add_summarized_turns(removed_turns)
    logger.info(
        "Context compacted: char=%s turns=%d tokens_est=%d summary_len=%d",
        character,
        removed_turns,
        mem.estimate_tokens(),
        len(merged),
    )
    return True
