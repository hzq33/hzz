"""Unit tests for Agent.maybe_compact (delegates to src.core.compaction)."""

from unittest.mock import MagicMock

from src.core.agent import Agent
from src.utils.config import AgentConfig, MemoryConfig


def _make_agent(monkeypatch, *, max_history_tokens=100, keep_turns=2, threshold=0.1):
    mem_cfg = MemoryConfig(
        max_history_tokens=max_history_tokens,
        enable_summarization=True,
        summarize_keep_turns=keep_turns,
        summarize_threshold=threshold,
    )
    config = AgentConfig(memory=mem_cfg, name="TestAgent")
    fake_llm = MagicMock()
    monkeypatch.setattr("src.core.agent.create_shared_llm", lambda *a, **kw: fake_llm)
    return Agent(config)


def _fill_history(agent: Agent, turns: int = 4):
    """Add `turns` rounds of user/assistant messages (each >100 chars)."""
    for i in range(turns * 2):
        role = "user" if i % 2 == 0 else "assistant"
        agent.memory.add_message(role, f"msg{i} " + "x" * 120)


def _turn_count(agent: Agent) -> int:
    return len(
        [m for m in agent.memory.get_messages() if m["role"] in ("user", "assistant")]
    )


class TestMaybeCompact:
    async def test_success_sets_summary(self, monkeypatch):
        agent = _make_agent(monkeypatch)
        _fill_history(agent, turns=4)  # 8 条

        async def fake(llm, *, character, messages, existing_summary="", max_tokens=500):
            return "压缩摘要"

        monkeypatch.setattr(
            "src.core.impersonation.summarizer.summarize_dialogue", fake
        )
        result = await agent.maybe_compact()
        assert result is True
        assert "压缩摘要" in agent.memory.get_summary()
        assert _turn_count(agent) == 4  # 成功后才删除，保留最新 keep_count 条

    async def test_empty_summary_keeps_messages(self, monkeypatch):
        agent = _make_agent(monkeypatch)
        _fill_history(agent, turns=4)

        async def fake(llm, *, character, messages, existing_summary="", max_tokens=500):
            return ""

        monkeypatch.setattr(
            "src.core.impersonation.summarizer.summarize_dialogue", fake
        )
        result = await agent.maybe_compact()
        assert result is False
        assert _turn_count(agent) == 8  # 空摘要：消息原封不动

    async def test_exception_keeps_messages(self, monkeypatch):
        agent = _make_agent(monkeypatch)
        _fill_history(agent, turns=4)

        async def fake(llm, *, character, messages, existing_summary="", max_tokens=500):
            raise RuntimeError("boom")

        monkeypatch.setattr(
            "src.core.impersonation.summarizer.summarize_dialogue", fake
        )
        result = await agent.maybe_compact()
        assert result is False
        # 异常时消息原封不动（peek 未删除），早期对话历史不丢失
        assert _turn_count(agent) == 8

    async def test_hard_trim_when_summary_fails(self, monkeypatch):
        agent = _make_agent(monkeypatch)
        _fill_history(agent, turns=35)  # 70 条，estimate > hard_limit(2000)

        async def fake(llm, *, character, messages, existing_summary="", max_tokens=500):
            return ""

        monkeypatch.setattr(
            "src.core.impersonation.summarizer.summarize_dialogue", fake
        )
        result = await agent.maybe_compact()
        assert result is True  # 硬裁剪兜底发生
        assert _turn_count(agent) == 4  # 强制保留 keep_count 条
        assert agent.memory.get_summarized_turns() == 33

    async def test_below_threshold_skips(self, monkeypatch):
        agent = _make_agent(monkeypatch, max_history_tokens=100000)
        _fill_history(agent, turns=1)
        result = await agent.maybe_compact()
        assert result is False
        assert agent.memory.get_summary() == ""

    async def test_disabled_skips(self, monkeypatch):
        agent = _make_agent(monkeypatch)
        agent.enable_summarization = False
        _fill_history(agent, turns=4)
        result = await agent.maybe_compact()
        assert result is False
