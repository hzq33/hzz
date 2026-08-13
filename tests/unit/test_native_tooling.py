"""Unit tests for shared native-tool-calling helpers.

覆盖 ``execute_tool_safely``（工具查找 / HITL 门 / 执行 / 错误包装 /
参数修正回调）与 ``build_native_messages``（role 过滤 / scope 前置 /
摘要追加）。
"""

from src.core.memory import ConversationMemory
from src.core.native_tooling import build_native_messages, execute_tool_safely
from src.tools.base import BaseTool, ToolResult
from src.tools.registry import ToolRegistry


class _RecordingTool(BaseTool):
    """记录 execute 收到的 kwargs，并返回固定结果。"""

    def __init__(self, name: str, result: ToolResult = ToolResult.ok("ok")):
        self.name = name
        self.result = result
        self.last_kwargs: dict | None = None

    async def execute(self, **kwargs):
        self.last_kwargs = dict(kwargs)
        return self.result


def _registry(*tools: BaseTool) -> ToolRegistry:
    reg = ToolRegistry()
    for t in tools:
        reg.register(t)
    return reg


class TestExecuteToolSafely:
    async def test_tool_not_found(self):
        reg = _registry()
        out = await execute_tool_safely("nope", {}, registry=reg)
        assert out == "Error: tool 'nope' not found"

    async def test_success_returns_output(self):
        reg = _registry(_RecordingTool("t", ToolResult.ok("hello")))
        out = await execute_tool_safely("t", {"a": 1}, registry=reg)
        assert out == "hello"

    async def test_failure_returns_error(self):
        reg = _registry(_RecordingTool("t", ToolResult.fail("boom")))
        out = await execute_tool_safely("t", {}, registry=reg)
        assert out == "Error: boom"

    async def test_empty_output_on_success(self):
        reg = _registry(_RecordingTool("t", ToolResult.ok("")))
        out = await execute_tool_safely("t", {}, registry=reg)
        assert out == ""

    async def test_hitl_denied_without_emit(self, monkeypatch):
        monkeypatch.setenv("AGENT_TOOL_HITL", "1")
        reg = _registry(_RecordingTool("execute_code", ToolResult.ok("ran")))
        out = await execute_tool_safely("execute_code", {}, registry=reg)
        assert out.startswith("Error:")
        assert "no approval channel" in out

    async def test_no_hitl_required_runs(self, monkeypatch):
        monkeypatch.setenv("AGENT_TOOL_HITL", "1")
        reg = _registry(_RecordingTool("file_operation", ToolResult.ok("done")))
        out = await execute_tool_safely(
            "file_operation", {"operation": "read"}, registry=reg
        )
        assert out == "done"

    async def test_adjust_args_replaces_args(self):
        tool = _RecordingTool("t")
        reg = _registry(tool)

        def adjust(name, args):
            assert name == "t"
            assert args == {"orig": 1}
            return {"adjusted": True}

        out = await execute_tool_safely(
            "t", {"orig": 1}, registry=reg, adjust_args=adjust
        )
        assert out == "ok"
        assert tool.last_kwargs == {"adjusted": True}

    async def test_adjust_args_async(self):
        tool = _RecordingTool("t")
        reg = _registry(tool)

        async def adjust(name, args):
            return {"async": True}

        out = await execute_tool_safely("t", {}, registry=reg, adjust_args=adjust)
        assert out == "ok"
        assert tool.last_kwargs == {"async": True}

    async def test_adjust_args_none_keeps_args(self):
        tool = _RecordingTool("t")
        reg = _registry(tool)

        def adjust(name, args):
            return None

        out = await execute_tool_safely(
            "t", {"keep": 1}, registry=reg, adjust_args=adjust
        )
        assert out == "ok"
        assert tool.last_kwargs == {"keep": 1}


class TestBuildNativeMessages:
    def test_role_filter_drops_tool(self):
        mem = ConversationMemory()
        mem.add_message("system", "sys")
        mem.add_message("user", "u1")
        mem.add_message("assistant", "a1")
        mem.add_message("tool", "t1")
        msgs = build_native_messages(memory=mem)
        assert [m["role"] for m in msgs] == ["system", "user", "assistant"]

    def test_scope_note_prepended(self):
        mem = ConversationMemory()
        mem.add_message("system", "sys")
        mem.add_message("user", "u1")
        msgs = build_native_messages(memory=mem, scope_note="【当前检索范围】")
        assert msgs[0] == {"role": "system", "content": "【当前检索范围】"}
        assert msgs[1] == {"role": "system", "content": "sys"}

    def test_blank_scope_note_skipped(self):
        mem = ConversationMemory()
        mem.add_message("user", "u1")
        msgs = build_native_messages(memory=mem, scope_note="   ")
        assert msgs == [{"role": "user", "content": "u1"}]

    def test_summary_appended(self):
        mem = ConversationMemory()
        mem.add_message("user", "u1")
        mem.set_summary("更早的摘要内容")
        msgs = build_native_messages(memory=mem)
        assert msgs[-1]["role"] == "system"
        assert "更早的摘要内容" in msgs[-1]["content"]

    def test_no_summary_no_scope(self):
        mem = ConversationMemory()
        mem.add_message("user", "u1")
        msgs = build_native_messages(memory=mem)
        assert msgs == [{"role": "user", "content": "u1"}]

    def test_scope_and_summary_both(self):
        mem = ConversationMemory()
        mem.add_message("user", "u1")
        mem.set_summary("摘要")
        msgs = build_native_messages(memory=mem, scope_note="范围")
        assert msgs[0]["content"] == "范围"
        assert "摘要" in msgs[-1]["content"]
