"""Shared native-tool-calling helpers for Agent and SwarmAgent.

``Agent._run_with_native_tools`` 与 ``SwarmAgent._native_tools_node`` 的工具
执行、消息装配逻辑逐行重复，本模块将其收敛为一处：

- :func:`execute_tool_safely` —— 工具查找 + HITL 门 + 执行 + 错误包装
- :func:`build_native_messages` —— role 过滤 + scope note 前置 + 摘要追加

两个入口的差异（session_id / emit / scope 注入）通过可选参数与回调承载，
调用方行为保持不变。
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from src.shared.tool_approvals import gate_tool_execution

# 可选回调：对 args 做强制修正（如 SwarmAgent 的 novel_search 检索范围隔离）
AdjustArgs = Callable[[str, dict[str, Any]], dict[str, Any] | Awaitable[dict[str, Any]] | None]


async def execute_tool_safely(
    name: str,
    args: dict[str, Any],
    *,
    registry: Any,
    session_id: str = "",
    emit: Any = None,
    adjust_args: AdjustArgs | None = None,
) -> str:
    """工具查找 + HITL 门 + 执行 + 错误包装（Agent / SwarmAgent 共用）。

    Args:
        name: 工具名。
        args: 工具参数。
        registry: ToolRegistry 实例。
        session_id: 会话 id（CLI/后台无会话时传空串）。
        emit: 可选异步回调 ``(event_type, data_dict)``，用于 SSE 审批通道。
        adjust_args: 可选回调 ``(name, args) -> args``，在 HITL 门前修正参数；
            SwarmAgent 用它把 novel_search 强制限定在当前检索范围内。

    Returns:
        工具输出文本；出错时返回 ``Error: ...`` 前缀的字符串。
    """
    # registry.get 在工具不存在时抛 ToolNotFoundError，这里用 has() 显式判断，
    # 实现"工具不存在返回 Error 字符串"的意图（旧代码的 ``if tool is None`` 是死分支）。
    if not registry.has(name):
        return f"Error: tool '{name}' not found"
    tool = registry.get(name)

    if adjust_args is not None:
        adjusted = adjust_args(name, args or {})
        if isinstance(adjusted, Awaitable):
            adjusted = await adjusted
        if isinstance(adjusted, dict):
            args = adjusted

    denied = await gate_tool_execution(
        session_id=session_id,
        tool_name=name,
        tool_args=args or {},
        emit=emit,
    )
    if denied:
        return f"Error: {denied}"

    result = await tool.execute(**args)
    if result.success:
        return result.output or ""
    return f"Error: {result.error}"


def build_native_messages(*, memory: Any, scope_note: str = "") -> list[dict[str, Any]]:
    """构建 native 工具调用的消息列表（Agent / SwarmAgent 共用）。

    - role 过滤：丢弃 tool 等协议消息，仅保留 system/user/assistant。
    - scope_note（可选）：前置为第一条 system 消息（SwarmAgent 检索范围）。
    - 摘要：把 ``memory.get_summary()`` 追加为 system 消息（防遗忘/防跨轮矛盾）。

    Args:
        memory: ConversationMemory 实例。
        scope_note: 可选检索范围说明；空串则跳过。

    Returns:
        可直接传给 ``achat_with_tools`` 的消息列表。
    """
    messages = [
        m for m in memory.get_messages()
        if m.get("role") in ("system", "user", "assistant")
    ]

    scope_note = (scope_note or "").strip()
    if scope_note:
        messages = [{"role": "system", "content": scope_note}, *messages]

    summary = memory.get_summary()
    if summary:
        messages.append({
            "role": "system",
            "content": (
                "## 更早的对话摘要（已确认事实，回答时不得与之矛盾；"
                "摘要之外未提及的细节不要自行补充）\n" + summary
            ),
        })
    return messages
