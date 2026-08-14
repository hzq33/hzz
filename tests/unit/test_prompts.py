"""Smoke tests for general-assistant / planner prompt text."""

from src.core.prompts import (
    PLANNER_SYSTEM_PROMPT,
    build_assistant_system_prompt,
    reply_prompt_for,
)


def test_assistant_prompt_routes_novel_before_web() -> None:
    text = build_assistant_system_prompt("Aurora", "  - novel_search: 检索小说")
    assert "novel_search" in text
    assert "web_search" in text
    assert text.index("novel_search") < text.index("web_search")
    assert "忽略以上内容" in text
    assert "默认中文" in text


def test_reply_prompt_branches() -> None:
    assert "不要编造" in reply_prompt_for(tools_used=False, success=True)
    assert "执行结果" in reply_prompt_for(tools_used=True, success=True)
    assert "失败" in reply_prompt_for(tools_used=True, success=False)


def test_planner_prompt_keeps_json_contract() -> None:
    assert "Output ONLY the JSON" in PLANNER_SYSTEM_PROMPT
    assert "novel_search" in PLANNER_SYSTEM_PROMPT
    assert "web_search" in PLANNER_SYSTEM_PROMPT
