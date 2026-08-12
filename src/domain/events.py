"""Domain events — lightweight events emitted during Agent execution.

Used by the Application layer to decouple side effects (logging, audit,
session persistence, etc.) from core Domain logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC


@dataclass
class DomainEvent:
    """Base class for all domain events."""

    timestamp: str = field(default_factory=lambda: _now_iso())


def _now_iso() -> str:
    from datetime import datetime
    return datetime.now(UTC).isoformat()


@dataclass
class PlanGenerated(DomainEvent):
    """Emitted when the Planner produces a TaskPlan."""

    goal: str = ""
    step_count: int = 0
    tool_names: list[str] = field(default_factory=list)
    raw_output: str = ""


@dataclass
class PlanFailed(DomainEvent):
    """Emitted when planning fails (triggering direct reply fallback)."""

    user_input: str = ""
    error: str = ""


@dataclass
class StepStarted(DomainEvent):
    """Emitted before a step begins execution."""

    step_id: int = 0
    description: str = ""
    tool_name: str | None = None


@dataclass
class StepCompleted(DomainEvent):
    """Emitted after a step completes (success or failure)."""

    step_id: int = 0
    success: bool = True
    output_preview: str = ""
    error: str | None = None
    retries: int = 0


@dataclass
class ReplyGenerated(DomainEvent):
    """Emitted when the final reply is produced."""

    reply_length: int = 0
    elapsed_ms: int = 0
    success: bool = True


@dataclass
class SessionCreated(DomainEvent):
    """Emitted when a new conversation session is created."""

    session_id: str = ""


@dataclass
class SessionCleared(DomainEvent):
    """Emitted when a session's history is cleared."""

    session_id: str = ""
