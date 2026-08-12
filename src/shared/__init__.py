"""Shared infrastructure for the Agent + RAG platform.

Provides unified LLM client, configuration bridge, and embedding interface
that all components (Agent Framework, rag, upload_serve, RAG_Parser_Serve)
can use.
"""

from .async_jobs import AsyncJobRunner, AsyncJobStore, JobRecord, get_job_runner, get_job_store
from .config_bridge import SharedConfig, load_shared_config
from .llm import SharedLLMClient
from .telemetry import init_telemetry, shutdown_telemetry, span, telemetry_enabled

__all__ = [
    "SharedLLMClient",
    "SharedConfig",
    "load_shared_config",
    "init_telemetry",
    "shutdown_telemetry",
    "span",
    "telemetry_enabled",
    "AsyncJobStore",
    "AsyncJobRunner",
    "JobRecord",
    "get_job_store",
    "get_job_runner",
]
