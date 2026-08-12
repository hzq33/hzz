"""llm.py mixin 拆分脚本 — 提取 resilience 方法到 _llm_resilience mixin。

用法: python scripts/dev/refactor/split_llm.py
"""
from pathlib import Path

SRC = Path("src/shared/llm.py")
PKG = Path("src/shared")
lines = SRC.read_text(encoding="utf-8").splitlines(keepends=True)


def get(start: int, end: int) -> str:
    return "".join(lines[start - 1 : end])


# resilience 方法（181-406）：
# _is_retryable(181-186), _switch_to_fallback(188-206), _circuit_threshold(208-214),
# _circuit_open_sec(216-222), _jitter_enabled(224-232), _circuit_blocked(234-235),
# _emit_circuit(237-243), _open_circuit(245-251), _ensure_circuit_allows(253-264),
# _note_success(266-270), _note_failure(272-281), _retry_after_seconds(283-298),
# _backoff_seconds(300-312), _on_failure(314-323), _try_revert(325-350),
# _log_usage(352-391), _record_stream_usage(393-406)
resilience = get(181, 406)

head = '''"""SharedLLMClient resilience mixin — circuit breaker, fallback, retry, usage.

Extracted from the former monolithic ``llm.py``; logic unchanged.
Mixin methods share instance state (``self._using_fallback`` / ``self._circuit_*``).
"""

from __future__ import annotations

import logging
import os
import random
import time
from typing import Any

logger = logging.getLogger("agent")


class LLMResilienceMixin:
    """Circuit breaker / fallback / retry / usage-tracking methods."""


'''

(PKG / "llm_resilience.py").write_text(head + resilience, encoding="utf-8")
print(f"wrote llm_resilience.py ({len((head+resilience).splitlines())} lines)")

import re
methods = re.findall(r"^    (?:async )?def (\w+)", resilience, re.MULTILINE)
print("mixin 方法:", methods)
