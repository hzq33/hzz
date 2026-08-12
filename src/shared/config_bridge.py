"""Shared configuration bridge.

Unifies configuration from multiple sources:
- config.yaml (Agent Framework)
- rag/config.py (RAG system)
- settings.json (RAG runtime overrides)

Provides a single SharedConfig dataclass accessible from all components.
"""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

# Project root (two levels up from src/shared/)
PROJECT_ROOT = Path(__file__).parent.parent.parent


@dataclass
class LLMProviderConfig:
    """Configuration for an LLM provider endpoint."""

    model: str = "deepseek-v4-flash"
    api_key: str = ""
    base_url: str = "https://api.deepseek.com"
    temperature: float = 0.7
    max_tokens: int = 4096


@dataclass
class RAGConfig:
    """RAG system configuration."""

    top_k: int = 10
    similarity_threshold: float = 0.3
    reranker_enabled: bool = True
    reranker_top_n: int = 5
    embedding_model: str = ""
    reranker_model: str = ""
    max_sessions: int = 5


@dataclass
class AgentLLMConfig:
    """Agent Framework LLM configuration."""

    name: str = "ModularAgent"
    model: str = "deepseek-v4-flash"
    api_key: str = ""
    base_url: str = "https://api.deepseek.com"
    temperature: float = 0.7
    max_tokens: int = 4096
    max_retries: int = 3
    max_turns: int = 10


@dataclass
class SharedConfig:
    """Unified configuration for all four components.

    Attributes:
        project_root: Absolute path to the project root.
        llm: Agent Framework LLM config.
        rag: RAG system config.
        rag_primary: RAG primary LLM endpoint.
        rag_fallback: RAG fallback LLM endpoint.
        memory: Memory configuration.
        tools: Tools configuration.
        logging: Logging configuration.
        raw: Raw parsed YAML dict for custom access.
    """

    project_root: Path = field(default_factory=lambda: PROJECT_ROOT)

    # Agent Framework
    llm: AgentLLMConfig = field(default_factory=AgentLLMConfig)

    # RAG system
    rag: RAGConfig = field(default_factory=RAGConfig)
    rag_primary: LLMProviderConfig = field(default_factory=LLMProviderConfig)
    rag_fallback: LLMProviderConfig = field(default_factory=LLMProviderConfig)

    # Memory
    memory_max_history_tokens: int = 8000
    memory_enable_summarization: bool = False
    memory_summarize_keep_turns: int = 8
    memory_summarize_threshold: float = 0.8

    # Tools
    tools_builtin: list[str] = field(default_factory=lambda: ["web_search", "execute_code", "file_operation", "rag"])
    tools_plugins: list[str] = field(default_factory=list)

    # Logging
    log_level: str = "INFO"
    log_file: str = "agent.log"

    # Raw YAML data
    raw: dict[str, Any] = field(default_factory=dict)


def _resolve_env(value: str) -> str:
    """Resolve ${VAR_NAME} environment variable placeholders."""
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        return os.environ.get(value[2:-1], "")
    return value


def load_shared_config(yaml_path: str | None = None) -> SharedConfig:
    """Load unified configuration from YAML, env vars, and runtime settings.

    Order of precedence (later overrides earlier):
        1. Default values
        2. config.yaml
        3. Environment variables (via ${VAR_NAME} in YAML)
        4. rag/settings.json (runtime overrides from web UI)

    Args:
        yaml_path: Path to config.yaml. Defaults to PROJECT_ROOT/config.yaml.

    Returns:
        A fully-resolved SharedConfig instance.
    """
    if yaml_path is None:
        yaml_path = str(PROJECT_ROOT / "config.yaml")

    # Load .env
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        try:
            load_dotenv(env_file, encoding="utf-8-sig")
        except UnicodeDecodeError:
            # Try with UTF-16 (Windows common)
            try:
                load_dotenv(env_file, encoding="utf-16")
            except Exception:
                pass

    # Load YAML
    raw: dict[str, Any] = {}
    if os.path.isfile(yaml_path):
        with open(yaml_path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

    # Resolve env vars recursively
    def _resolve_dict(d: dict) -> dict:
        result = {}
        for k, v in d.items():
            if isinstance(v, dict):
                result[k] = _resolve_dict(v)
            elif isinstance(v, list):
                result[k] = [_resolve_env(x) if isinstance(x, str) else x for x in v]
            elif isinstance(v, str):
                result[k] = _resolve_env(v)
            else:
                result[k] = v
        return result

    raw = _resolve_dict(raw)

    # Load RAG runtime settings
    settings_file = PROJECT_ROOT / "rag" / "settings.json"
    runtime_settings: dict = {}
    if settings_file.exists():
        try:
            with open(settings_file, encoding="utf-8") as f:
                runtime_settings = json.load(f)
        except Exception:
            pass

    # Parse sections
    agent_section = raw.get("agent", {})
    memory_section = raw.get("memory", {})
    tools_section = raw.get("tools", {})
    logging_section = raw.get("logging", {})
    rag_section = raw.get("rag", {})

    # Build Agent LLM config
    llm = AgentLLMConfig(
        name=agent_section.get("name", "ModularAgent"),
        model=agent_section.get("model", "deepseek-v4-flash"),
        api_key=agent_section.get("api_key", ""),
        base_url=agent_section.get("base_url", "https://api.deepseek.com"),
        temperature=float(agent_section.get("temperature", 0.7)),
        max_tokens=int(agent_section.get("max_tokens", 4096)),
        max_retries=int(agent_section.get("max_retries", 3)),
        max_turns=int(agent_section.get("max_turns", 10)),
    )

    # Build RAG primary LLM config
    rag_primary = LLMProviderConfig(
        model=runtime_settings.get("model")
        or rag_section.get("primary", {}).get("model")
        or os.getenv("DEEPSEEK_MODEL", "deepseek-r1:1.5b"),
        api_key=runtime_settings.get("api_key")
        or rag_section.get("primary", {}).get("api_key")
        or os.getenv("DEEPSEEK_API_KEY", ""),
        base_url=runtime_settings.get("api_base")
        or rag_section.get("primary", {}).get("base_url")
        or os.getenv("DEEPSEEK_BASE_URL", "http://localhost:9527/api/v1"),
        temperature=float(runtime_settings.get("temperature", 0.7)),
        max_tokens=512,
    )

    # Build RAG fallback LLM config
    rag_fallback = LLMProviderConfig(
        model=rag_section.get("fallback", {}).get("model")
        or os.getenv("DEEPSEEK_FALLBACK_MODEL", "deepseek-v4-pro"),
        api_key=runtime_settings.get("fallback_api_key")
        or rag_section.get("fallback", {}).get("api_key")
        or os.getenv("DEEPSEEK_FALLBACK_API_KEY", ""),
        base_url=runtime_settings.get("fallback_api_base")
        or rag_section.get("fallback", {}).get("base_url")
        or os.getenv("DEEPSEEK_FALLBACK_BASE_URL", "https://api.deepseek.com"),
        temperature=float(runtime_settings.get("temperature", 0.7)),
        max_tokens=1024,
    )

    # Build RAG system config
    rag = RAGConfig(
        top_k=int(runtime_settings.get("top_k", rag_section.get("top_k", 10))),
        similarity_threshold=float(
            runtime_settings.get("similarity_threshold", rag_section.get("similarity_threshold", 0.3))
        ),
        reranker_enabled=bool(runtime_settings.get("reranker_enabled", rag_section.get("reranker_enabled", True))),
        reranker_top_n=int(runtime_settings.get("reranker_top_n", rag_section.get("reranker_top_n", 5))),
        embedding_model=rag_section.get("embedding_model", ""),
        reranker_model=rag_section.get("reranker_model", ""),
        max_sessions=int(runtime_settings.get("max_sessions", rag_section.get("max_sessions", 5))),
    )

    return SharedConfig(
        project_root=PROJECT_ROOT,
        llm=llm,
        rag=rag,
        rag_primary=rag_primary,
        rag_fallback=rag_fallback,
        memory_max_history_tokens=int(memory_section.get("max_history_tokens", 8000)),
        memory_enable_summarization=bool(memory_section.get("enable_summarization", False)),
        memory_summarize_keep_turns=int(memory_section.get("summarize_keep_turns", 8)),
        memory_summarize_threshold=float(memory_section.get("summarize_threshold", 0.8)),
        tools_builtin=tools_section.get("builtin", ["web_search", "execute_code", "file_operation", "rag"]),
        tools_plugins=tools_section.get("plugins", []),
        log_level=logging_section.get("level", "INFO"),
        log_file=logging_section.get("file", "agent.log"),
        raw=raw,
    )
