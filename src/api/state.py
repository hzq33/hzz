"""Shared mutable runtime state and configuration helpers for API routers."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import HTTPException

from src.application.conversation_service import ConversationService
from src.application.event_bus import EventBus
from src.application.impersonation_sessions import ImpersonationSessionService
from src.domain.events import SessionCleared, SessionCreated
from src.utils.errors import ConfigurationError

logger = logging.getLogger("agent_server")

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.yaml"

config_error: str | None = None
cached_tools: list | None = None
config_loaded = False
event_bus: EventBus
conversation: ConversationService
imp_sessions: ImpersonationSessionService
imp_store = None
# 上传新书后置 True：下次访问 store 时强制重建（新 LanceDB 连接 + 已重建的
# 共享 keyword 索引）——根治"运行时上传后新书检索失效"（须重启才恢复）。
store_dirty = False


def reset_runtime_state() -> None:
    """Recreate process-local state (also useful for module-reload tests)."""
    global config_error, cached_tools, config_loaded
    global event_bus, conversation, imp_sessions, imp_store

    config_error = None
    cached_tools = None
    config_loaded = False
    event_bus = EventBus()
    conversation = ConversationService(max_sessions=5, event_bus=event_bus)
    imp_sessions = ImpersonationSessionService()
    imp_store = None
    event_bus.subscribe(
        SessionCreated,
        lambda event: logger.info("Session created: %s", event.session_id),
    )
    event_bus.subscribe(
        SessionCleared,
        lambda event: logger.info("Session cleared: %s", event.session_id),
    )


def load_config():
    """Load config gracefully, returning None when validation fails."""
    global config_error
    try:
        from src.utils.config import load_config as load_agent_config

        config = load_agent_config(str(CONFIG_PATH))
        config_error = None
        return config
    except ConfigurationError as exc:
        config_error = str(exc)
        logger.warning("Config load failed: %s", exc)
        return None
    except Exception as exc:
        config_error = str(exc)
        logger.warning("Config load failed: %s", exc)
        return None


def require_config():
    """Return validated config and apply server-level settings once."""
    global config_loaded
    config = load_config()
    if config is None:
        raise HTTPException(
            status_code=503,
            detail="Agent not configured: set DEEPSEEK_API_KEY in .env or environment",
        )
    if not config_loaded:
        conversation.max_sessions = getattr(config, "max_sessions", 5)
        config_loaded = True
        logger.info("Server config applied: max_sessions=%d", conversation.max_sessions)
    return config


def get_tools_info() -> list:
    """Return builtin tool metadata, cached after first construction."""
    global cached_tools
    if cached_tools is not None:
        return cached_tools

    from src.core.agent import Agent
    from src.tools.bootstrap import register_builtin_tools

    config = require_config()
    agent = Agent(config)
    register_builtin_tools(agent.tool_registry, config)
    cached_tools = [
        {"name": tool.name, "description": tool.description}
        for tool in agent.tool_registry.list_all()
    ]
    return cached_tools


reset_runtime_state()
