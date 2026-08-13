"""Central default values for LLM endpoints and model names.

这些常量是各模块散落硬编码的唯一来源，避免改端点/模型名时全局 grep 漏改。
"""

import os
import re
from pathlib import Path
from typing import Any

DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-flash"
DEFAULT_DEEPSEEK_FALLBACK_MODEL = "deepseek-v4-pro"


def max_tool_rounds() -> int:
    """Native tool-calling loop 的最大轮数（AGENT_TOOL_MAX_ROUNDS 覆盖，默认 5）。"""
    try:
        return max(1, int(os.getenv("AGENT_TOOL_MAX_ROUNDS", "5")))
    except ValueError:
        return 5


def resolve_env_placeholders(value: str) -> str:
    """Replace ${VAR} placeholders anywhere in `value` with env values.

    缺失的环境变量替换为空字符串；严格校验（如 api_key 非空）由调用方负责。
    这是 config.yaml 各读取路径统一使用的 ${VAR} 解析实现。
    """
    if not isinstance(value, str):
        return value
    return re.sub(r"\$\{(\w+)\}", lambda m: os.getenv(m.group(1), ""), value)


_yaml_cache: dict[str, tuple[float, Any]] = {}


def load_yaml_cached(path: str):
    """Load a YAML file with a process-level cache (invalidated by mtime).

    Returns the parsed YAML (dict or None for empty file)；空文件处理由调用方决定。
    """
    p = Path(path)
    try:
        mtime = p.stat().st_mtime
    except OSError:
        mtime = 0.0
    cached = _yaml_cache.get(str(p))
    if cached is not None and cached[0] == mtime:
        return cached[1]
    import yaml

    with open(p, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    _yaml_cache[str(p)] = (mtime, data)
    return data
