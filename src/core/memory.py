"""Memory management for conversation history and working state."""

import logging
from typing import Any

from src.utils.errors import MemoryError

logger = logging.getLogger("agent")


class ConversationMemory:
    """Stores and manages the conversation history.

    Supports role-based messages (system, user, assistant, tool) and
    automatic truncation based on estimated token count.

    Attributes:
        max_tokens: Maximum estimated tokens before truncation kicks in.
        messages: The list of message dicts in chronological order.
    """

    # Approximate tokens-per-character ratio for estimation
    _TOKENS_PER_CHAR: float = 0.25
    # Minimum number of messages to keep, regardless of token count
    _MIN_MESSAGES: int = 4

    def __init__(self, max_tokens: int = 8000, truncate_enabled: bool = True) -> None:
        """Initialize conversation memory.

        Args:
            max_tokens: Maximum estimated tokens before oldest messages are trimmed.
            truncate_enabled: False when context summarization owns trimming
                (older turns folded into a summary instead of dropped).
        """
        self.max_tokens: int = max_tokens
        self.truncate_enabled: bool = truncate_enabled
        self._messages: list[dict[str, str]] = []
        # 上下文压缩摘要：最早轮次被压缩后写入这里，不随截断丢弃。
        self._summary: str = ""
        # 累计压缩掉的轮次数（前端监控展示用）。
        self._summarized_turns: int = 0
        logger.debug(
            "ConversationMemory initialized with max_tokens=%d truncate_enabled=%s",
            max_tokens, truncate_enabled,
        )

    def add_message(self, role: str, content: str) -> None:
        """Add a message to the conversation history.

        Args:
            role: Message role (system, user, assistant, tool).
            content: The message text.

        Raises:
            MemoryError: If role is invalid.
        """
        valid_roles = {"system", "user", "assistant", "tool"}
        if role not in valid_roles:
            raise MemoryError(f"Invalid message role: '{role}'. Must be one of {valid_roles}.")
        self._messages.append({"role": role, "content": content})
        if self.truncate_enabled:
            self._truncate()
        logger.debug("Added message: role=%s, total_messages=%d", role, len(self._messages))

    def get_messages(self) -> list[dict[str, str]]:
        """Return a copy of all messages in chronological order.

        Returns:
            A list of message dicts with 'role' and 'content' keys.
        """
        return list(self._messages)

    def get_last_n(self, n: int) -> list[dict[str, str]]:
        """Return the last n messages.

        Args:
            n: Number of most recent messages to return.

        Returns:
            A list of the last n message dicts.
        """
        return self._messages[-n:] if n > 0 else []

    def set_summary(self, summary: str) -> None:
        """Set the compressed-context summary (survives truncation)."""
        self._summary = (summary or "").strip()
        logger.debug("Conversation memory summary set (len=%d)", len(self._summary))

    def get_summary(self) -> str:
        """Return the compressed-context summary, or empty string."""
        return self._summary

    def add_summarized_turns(self, turns: int) -> None:
        """Accumulate count of turns folded into the summary (for UI metrics)."""
        if turns > 0:
            self._summarized_turns += int(turns)

    def get_summarized_turns(self) -> int:
        """Total turns collapsed into the summary so far."""
        return self._summarized_turns

    def peek_oldest(self, keep: int) -> list[dict[str, str]]:
        """Return the messages ``drop_oldest(keep)`` would remove, without removing.

        供"先摘要成功再删除"的压缩流程使用：摘要失败时消息原封不动，
        无需回滚（替代旧的 drop→restore 模式）。
        """
        start = 1 if (self._messages and self._messages[0]["role"] == "system") else 0
        removable = self._messages[start:]
        if len(removable) <= keep:
            return []
        return list(removable[:-keep])

    def drop_oldest(self, keep: int) -> list[dict[str, str]]:
        """Remove oldest non-system messages beyond the newest ``keep``.

        Returns the removed messages (in chronological order) so the caller
        can fold them into a summary. The system message (index 0) is never
        removed.
        """
        start = 1 if (self._messages and self._messages[0]["role"] == "system") else 0
        removable = self._messages[start:]
        if len(removable) <= keep:
            return []
        removed = removable[:-keep]
        self._messages = self._messages[:start] + removable[-keep:]
        logger.debug(
            "Dropped %d oldest messages, kept %d (total=%d)",
            len(removed), keep, len(self._messages),
        )
        return removed

    def clear(self) -> None:
        """Remove all messages from history."""
        self._messages.clear()
        logger.debug("Conversation memory cleared.")

    def set_system_message(self, content: str) -> None:
        """Set or replace the system message at the beginning of the history.

        Args:
            content: The system message content.
        """
        if self._messages and self._messages[0]["role"] == "system":
            self._messages[0]["content"] = content
        else:
            self._messages.insert(0, {"role": "system", "content": content})
        logger.debug("System message set.")

    def estimate_tokens(self) -> int:
        """Estimate the total token count of all messages.

        Uses a rough character-to-token ratio for estimation.

        Returns:
            Estimated token count.
        """
        total_chars = sum(len(m["content"]) for m in self._messages)
        return int(total_chars * self._TOKENS_PER_CHAR)

    def _truncate(self) -> None:
        """Truncate oldest messages if estimated token count exceeds limit.

        The system message (if present) and the most recent messages are preserved.
        """
        while (
            len(self._messages) > self._MIN_MESSAGES
            and self.estimate_tokens() > self.max_tokens
        ):
            # Remove the oldest non-system message
            start = 1 if (self._messages and self._messages[0]["role"] == "system") else 0
            if start < len(self._messages):
                removed = self._messages.pop(start)
                logger.debug(
                    "Truncated message (tokens_est=%d): role=%s",
                    self.estimate_tokens(),
                    removed["role"],
                )
            else:
                break

    def __len__(self) -> int:
        return len(self._messages)

    def __repr__(self) -> str:
        return f"ConversationMemory(messages={len(self._messages)}, tokens_est={self.estimate_tokens()})"


class WorkingMemory:
    """Key-value store for intermediate task execution state.

    Used to pass data between pipeline steps (e.g., tool results).
    """

    def __init__(self) -> None:
        self._store: dict[str, Any] = {}
        logger.debug("WorkingMemory initialized.")

    def set(self, key: str, value: Any) -> None:
        """Store a value under a key.

        Args:
            key: The lookup key.
            value: Any Python value to store.
        """
        self._store[key] = value
        logger.debug("WorkingMemory set: %s", key)

    def get(self, key: str, default: Any = None) -> Any:
        """Retrieve a stored value.

        Args:
            key: The lookup key.
            default: Value to return if key is not found.

        Returns:
            The stored value or default.
        """
        result = self._store.get(key, default)
        logger.debug("WorkingMemory get: %s (found=%s)", key, result is not default or key in self._store)
        return result

    def delete(self, key: str) -> bool:
        """Remove a key from storage.

        Args:
            key: The key to delete.

        Returns:
            True if the key was found and removed.
        """
        if key in self._store:
            del self._store[key]
            logger.debug("WorkingMemory delete: %s", key)
            return True
        return False

    def clear(self) -> None:
        """Remove all stored values."""
        self._store.clear()
        logger.debug("WorkingMemory cleared.")

    def keys(self) -> list[str]:
        """Return all keys currently stored.

        Returns:
            List of key strings.
        """
        return list(self._store.keys())

    def __contains__(self, key: str) -> bool:
        return key in self._store

    def __repr__(self) -> str:
        return f"WorkingMemory(keys={list(self._store.keys())})"
