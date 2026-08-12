"""需求域 J — 安全：execute_code 默认禁用 / AST 拦截 / 文件沙箱 / SSE 脱敏。

覆盖 docs/REQUIREMENTS.md J-01 ~ J-05（上传安全 J-04 已在 test_A 覆盖）。
黑盒：工具注册走公开 register_builtin_tools，沙箱走公开 execute 方法；
_bocked_import 是安全门闩契约的验收点（J-01 明确要求的拦截行为）。
"""

from __future__ import annotations

from tests.conftest import FakeLLM, auth_headers


# ── J-01 execute_code ─────────────────────────────────────────


def test_execute_code_disabled_by_default(monkeypatch):
    """J-01：EXECUTE_CODE_ENABLED 未设置 → execute_code 不注册。"""
    from src.tools.registry import ToolRegistry
    from src.tools.bootstrap import register_builtin_tools
    from src.utils.config import load_config

    monkeypatch.delenv("EXECUTE_CODE_ENABLED", raising=False)
    config = load_config("config.yaml")
    assert config is not None
    registry = ToolRegistry()
    register_builtin_tools(registry, config)
    assert "execute_code" not in registry.list_names()


def test_execute_code_registers_when_enabled_and_listed(monkeypatch):
    """J-01：配置白名单含 execute_code 且 EXECUTE_CODE_ENABLED=true → 注册。"""
    from src.tools.registry import ToolRegistry
    from src.tools.bootstrap import register_builtin_tools
    from src.utils.config import load_config

    monkeypatch.setenv("EXECUTE_CODE_ENABLED", "true")
    config = load_config("config.yaml")
    config.tools.builtin = [*config.tools.builtin, "execute_code"]
    registry = ToolRegistry()
    register_builtin_tools(registry, config)
    assert "execute_code" in registry.list_names()


def test_blocked_import_os_forms():
    """J-01：AST 级 import 拦截——os 及其空白变体被拦。"""
    from src.tools.builtin_code import _blocked_import

    for code in (
        "import os",
        "import  os",  # 多空格
        "import\tos",  # 制表符
        "import os.path",
        "from os import getenv",
        "from    os    import  system",  # 空白变体
        "import subprocess",
    ):
        assert _blocked_import(code), f"应拦截: {code!r}"


def test_blocked_import_relative_import():
    """J-01：相对导入被拦截（纵深防御）。"""
    from src.tools.builtin_code import _blocked_import

    assert _blocked_import("from . import secret") is not None


def test_blocked_import_allows_safe_code():
    """J-01：安全代码不被误拦。"""
    from src.tools.builtin_code import _blocked_import

    for code in ("import math", "import json", "x = 1 + 2", "def f():\n    return 1"):
        assert _blocked_import(code) is None, f"不应拦截: {code!r}"


# ── J-02 文件沙箱 ─────────────────────────────────────────────


async def test_file_operation_blocks_escape(tmp_path):
    """J-02：../ 前缀逃逸被拒绝（路径解析拒绝，不 500）。"""
    from src.tools.builtin_file import FileOperationTool

    tool = FileOperationTool(working_dir=str(tmp_path / "workspace"))
    result = await tool.execute(operation="read", path="../secret.txt")
    assert not result.success
    assert "逃" in str(result.error) or "拒绝" in str(result.error)


async def test_file_operation_allows_internal_read(tmp_path):
    """J-02：工作目录内文件可读。"""
    from src.tools.builtin_file import FileOperationTool

    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "ok.txt").write_text("hello", encoding="utf-8")
    tool = FileOperationTool(working_dir=str(ws))
    result = await tool.execute(operation="read", path="ok.txt")
    assert result.success
    assert "hello" in str(result.output)


# ── J-05 SSE 与日志脱敏 ───────────────────────────────────────


def test_chat_stream_error_does_not_leak_traceback(api_client, monkeypatch):
    """J-05：LLM 异常时 SSE error 事件不泄漏内部堆栈/路径。"""
    from fastapi.testclient import TestClient

    from agent_server import app

    monkeypatch.setattr(app.state, "create_shared_llm", lambda *a, **kw: FakeLLM(exc=RuntimeError("boom")))
    import src.core.agent as core_agent

    monkeypatch.setattr(core_agent, "create_shared_llm", lambda *a, **kw: FakeLLM(exc=RuntimeError("boom")))
    r = api_client.post(
        "/api/v1/agent/chat/stream",
        json={"message": "你好"},
        headers=auth_headers(),
    )
    text = r.text
    assert "Traceback" not in text
    assert "D:\\tools" not in text and "site-packages" not in text
    assert "boom" in text  # 错误信息保留（脱敏后）
