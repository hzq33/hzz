"""Central default values for LLM endpoints and model names.

这些常量是各模块散落硬编码的唯一来源，避免改端点/模型名时全局 grep 漏改。
"""

import os

DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-flash"
DEFAULT_DEEPSEEK_FALLBACK_MODEL = "deepseek-v4-pro"


def max_tool_rounds() -> int:
    """Native tool-calling loop 的最大轮数（AGENT_TOOL_MAX_ROUNDS 覆盖，默认 5）。"""
    try:
        return max(1, int(os.getenv("AGENT_TOOL_MAX_ROUNDS", "5")))
    except ValueError:
        return 5
