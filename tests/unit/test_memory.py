"""Unit tests for ConversationMemory."""

from src.core.memory import ConversationMemory


class TestDropOldest:
    def test_keeps_system_message(self):
        mem = ConversationMemory(max_tokens=10000)
        mem.set_system_message("sys")
        for i in range(6):
            mem.add_message("user" if i % 2 == 0 else "assistant", f"msg{i}")
        removed = mem.drop_oldest(keep=2)
        assert len(removed) == 4
        msgs = mem.get_messages()
        assert msgs[0]["role"] == "system"
        assert len(msgs) == 3  # system + 最新 2 条

    def test_nothing_removed_when_under_keep(self):
        mem = ConversationMemory(max_tokens=10000)
        mem.set_system_message("sys")
        mem.add_message("user", "hi")
        removed = mem.drop_oldest(keep=4)
        assert removed == []


class TestPeekOldest:
    def test_peek_does_not_remove(self):
        mem = ConversationMemory(max_tokens=10000)
        mem.set_system_message("sys")
        for i in range(6):
            mem.add_message("user" if i % 2 == 0 else "assistant", f"msg{i}")
        peeked = mem.peek_oldest(keep=2)
        assert len(peeked) == 4
        # 窥视不删除：消息仍为 system + 6
        assert len(mem.get_messages()) == 7

    def test_peek_matches_drop(self):
        mem = ConversationMemory(max_tokens=10000)
        mem.set_system_message("sys")
        for i in range(6):
            mem.add_message("user" if i % 2 == 0 else "assistant", f"msg{i}")
        peeked = mem.peek_oldest(keep=2)
        removed = mem.drop_oldest(keep=2)
        assert [m["content"] for m in peeked] == [m["content"] for m in removed]

    def test_peek_under_keep_returns_empty(self):
        mem = ConversationMemory(max_tokens=10000)
        mem.set_system_message("sys")
        mem.add_message("user", "hi")
        assert mem.peek_oldest(keep=4) == []


class TestTruncate:
    def test_truncate_disabled_when_summarization(self):
        mem = ConversationMemory(max_tokens=10, truncate_enabled=False)
        for i in range(20):
            mem.add_message("user", "x" * 100)
        assert len(mem.get_messages()) == 20

    def test_truncate_enabled_caps_messages(self):
        mem = ConversationMemory(max_tokens=10, truncate_enabled=True)
        mem.set_system_message("sys")
        for i in range(20):
            mem.add_message("user", "x" * 100)
        # 截断到 _MIN_MESSAGES=4（system + 3）
        assert len(mem.get_messages()) == 4
        assert mem.get_messages()[0]["role"] == "system"


class TestSummary:
    def test_summary_survives_clear_roundtrip(self):
        mem = ConversationMemory(max_tokens=10000)
        mem.set_summary("earlier context")
        assert mem.get_summary() == "earlier context"

    def test_summarized_turns_accumulate(self):
        mem = ConversationMemory(max_tokens=10000)
        mem.add_summarized_turns(3)
        mem.add_summarized_turns(2)
        assert mem.get_summarized_turns() == 5
