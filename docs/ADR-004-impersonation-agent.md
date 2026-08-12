# ADR-004: Impersonation Agent

> Status: Accepted | 2026-07

## Context

Role-play needs multi-turn memory, novel retrieval, and optional tool facts in the final reply.

## Decision

Dedicated ImpersonationAgent with true token streaming, NovelRetrieval (4-channel), and tool results injected into the reply context.

## Consequences

- Frontend /impersonation uses dedicated SSE endpoints
- See NOVEL_RAG_DESIGN.md
