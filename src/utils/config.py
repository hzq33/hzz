"""Configuration management for the agent framework.

Loads and validates YAML configuration, providing a typed dataclass interface.
"""

import os
from dataclasses import dataclass, field
from typing import Any

import yaml

from src.utils.errors import ConfigurationError

from src.shared.defaults import (
    DEFAULT_DEEPSEEK_BASE_URL,
    DEFAULT_DEEPSEEK_MODEL,
    load_yaml_cached,
)


@dataclass
class MemoryConfig:
    """Memory-specific configuration."""

    max_history_tokens: int = 8000
    enable_summarization: bool = False
    summarize_keep_turns: int = 8
    summarize_threshold: float = 0.8


@dataclass
class ToolsConfig:
    """Tools-specific configuration."""

    builtin: list[str] = field(default_factory=lambda: ["web_search", "file_operation"])
    plugins: list[str] = field(default_factory=list)


@dataclass
class LoggingConfig:
    """Logging-specific configuration."""

    level: str = "INFO"
    file: str = "agent.log"


@dataclass
class AgentConfig:
    """Configuration for the Modular Agent.

    Attributes:
        name: Agent display name.
        model: OpenAI-compatible model identifier.
        fallback_model: Optional secondary model used when primary fails.
        api_key: API key for the LLM provider.
        base_url: Base URL for the API endpoint.
        temperature: Sampling temperature for LLM calls.
        max_tokens: Maximum tokens per LLM response.
        max_retries: Maximum retry attempts for failed steps.
        max_turns: Maximum conversation turns.
        memory: Memory-specific configuration.
        tools: Tools-specific configuration.
        logging: Logging-specific configuration.
    """

    name: str = "ModularAgent"
    model: str = DEFAULT_DEEPSEEK_MODEL
    fallback_model: str = ""
    api_key: str = ""
    base_url: str = DEFAULT_DEEPSEEK_BASE_URL
    temperature: float = 0.7
    max_tokens: int = 4096
    max_retries: int = 3
    max_turns: int = 10
    max_sessions: int = 5
    # None → auto-disable DeepSeek thinking (base_url/model 含 deepseek 时);
    # True/False → 显式覆盖（非 DeepSeek 供应商可设 False）。
    thinking_disabled: bool | None = None
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)

    def primary_llm_config(self) -> dict:
        """Build the primary SharedLLMClient provider dict."""
        return {
            "base_url": self.base_url,
            "api_key": self.api_key,
            "model": self.model,
        }

    def fallback_llm_config(self) -> dict | None:
        """Build the fallback provider dict, or None when unset."""
        if not self.fallback_model:
            return None
        return {
            "base_url": self.base_url,
            "api_key": self.api_key,
            "model": self.fallback_model,
        }


def _resolve_env_var(value: str) -> str:
    """Resolve ${VAR} placeholders in a string value (delegates to shared impl).

    缺失的环境变量替换为空字符串；api_key 等必填项的严格校验由 load_config
    后续的 validate 阶段负责。
    """
    from src.shared.defaults import resolve_env_placeholders

    return resolve_env_placeholders(value)


def load_config(config_path: str) -> AgentConfig:
    """Load and validate agent configuration from a YAML file.

    Environment variable placeholders (${VAR_NAME}) in the config are
    automatically resolved.

    Args:
        config_path: Absolute or relative path to the YAML config file.

    Returns:
        A fully-resolved AgentConfig instance.

    Raises:
        ConfigurationError: If the file is missing, malformed, or missing
            required fields.
    """
    if not os.path.isfile(config_path):
        raise ConfigurationError(
            f"Configuration file not found: {config_path}",
            missing_key=config_path,
        )

    try:
        raw: dict[str, Any] = load_yaml_cached(config_path)
    except yaml.YAMLError as e:
        raise ConfigurationError(f"Failed to parse YAML: {e}") from e

    if raw is None:
        raise ConfigurationError("Configuration file is empty.")

    # Resolve environment variables in all string values
    def _resolve_dict(d: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for k, v in d.items():
            if isinstance(v, dict):
                result[k] = _resolve_dict(v)
            elif isinstance(v, list):
                result[k] = [_resolve_env_var(item) if isinstance(item, str) else item for item in v]
            elif isinstance(v, str):
                result[k] = _resolve_env_var(v)
            else:
                result[k] = v
        return result

    resolved = _resolve_dict(raw)

    agent_section = resolved.get("agent", {})
    memory_section = resolved.get("memory", {})
    tools_section = resolved.get("tools", {})
    logging_section = resolved.get("logging", {})

    # Validate required fields
    api_key = agent_section.get("api_key", "")
    if not api_key:
        raise ConfigurationError(
            "Missing required field: agent.api_key. Provide it in config or "
            "set the corresponding environment variable.",
            missing_key="api_key",
        )

    model = agent_section.get("model", DEFAULT_DEEPSEEK_MODEL)
    if not model:
        raise ConfigurationError(
            "Missing required field: agent.model.",
            missing_key="model",
        )

    config = AgentConfig(
        name=agent_section.get("name", "ModularAgent"),
        model=model,
        fallback_model=str(agent_section.get("fallback_model", "") or ""),
        api_key=api_key,
        base_url=agent_section.get("base_url", DEFAULT_DEEPSEEK_BASE_URL),
        temperature=float(agent_section.get("temperature", 0.7)),
        max_tokens=int(agent_section.get("max_tokens", 4096)),
        max_retries=int(agent_section.get("max_retries", 3)),
        max_turns=int(agent_section.get("max_turns", 10)),
        max_sessions=int(agent_section.get("max_sessions", 5)),
        memory=MemoryConfig(
            max_history_tokens=int(memory_section.get("max_history_tokens", 8000)),
            enable_summarization=bool(memory_section.get("enable_summarization", False)),
            summarize_keep_turns=int(memory_section.get("summarize_keep_turns", 8)),
            summarize_threshold=float(memory_section.get("summarize_threshold", 0.8)),
        ),
        tools=ToolsConfig(
            builtin=tools_section.get("builtin", ["web_search", "file_operation"]),
            plugins=tools_section.get("plugins", []),
        ),
        logging=LoggingConfig(
            level=logging_section.get("level", "INFO"),
            file=logging_section.get("file", "agent.log"),
        ),
    )

    return config


def validate_config(config: AgentConfig) -> None:
    """Perform additional validation on an already-loaded AgentConfig.

    Args:
        config: The AgentConfig instance to validate.

    Raises:
        ConfigurationError: If any validation check fails.
    """
    if config.temperature < 0.0 or config.temperature > 2.0:
        raise ConfigurationError("temperature must be between 0.0 and 2.0.")
    if config.max_retries < 0:
        raise ConfigurationError("max_retries must be >= 0.")
    if config.max_turns < 1:
        raise ConfigurationError("max_turns must be >= 1.")
    if config.memory.max_history_tokens < 100:
        raise ConfigurationError("max_history_tokens must be >= 100.")
