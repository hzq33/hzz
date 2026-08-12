# ADR-003: LangGraph Swarm Routing

> Status: Accepted | 2026-07

## Context

Need a single /chat entry that can switch between general Plan-Execute-Reply and character impersonation without duplicating HTTP handlers.

## Decision

Use LangGraph StateGraph (SwarmAgent) with a classify node routing to general or character subgraphs. Both /chat and /chat/stream call SwarmAgent.run_stream.

## Consequences

- One auth / session / telemetry path
- Character mode shares tools via ImpersonationAgent
- See AGENT_FLOW.md
