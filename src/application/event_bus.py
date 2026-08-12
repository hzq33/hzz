"""Lightweight in-process event bus — no external dependencies.

Handlers subscribe by event type. All handlers run synchronously
in the same event loop (fire-and-forget for side effects).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable

from src.domain.events import DomainEvent

logger = logging.getLogger("agent")

EventHandler = Callable[[DomainEvent], None]


class EventBus:
    """Simple synchronous event pub/sub.

    Usage:
        bus = EventBus()
        bus.subscribe(PlanGenerated, lambda e: log.info("Plan: %s", e.goal))
        bus.publish(PlanGenerated(goal="search web"))
    """

    def __init__(self):
        self._handlers: dict[type, list[EventHandler]] = defaultdict(list)

    def subscribe(self, event_type: type, handler: EventHandler) -> None:
        """Register a handler for a specific event type."""
        self._handlers[event_type].append(handler)

    def unsubscribe(self, event_type: type, handler: EventHandler) -> None:
        """Remove a handler."""
        handlers = self._handlers.get(event_type, [])
        if handler in handlers:
            handlers.remove(handler)

    def publish(self, event: DomainEvent) -> None:
        """Publish an event to all registered handlers.

        Handlers run in the calling thread. If a handler raises,
        the exception is logged and other handlers continue.
        """
        for handler in self._handlers.get(type(event), []):
            try:
                handler(event)
            except Exception:
                logger.exception(
                    "Event handler %s failed for %s",
                    handler.__name__,
                    type(event).__name__,
                )

    def clear(self) -> None:
        """Remove all handlers."""
        self._handlers.clear()
