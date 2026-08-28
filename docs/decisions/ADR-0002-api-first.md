# ADR-0002 — API-first application boundary

**Status:** ACCEPTED  
**Date:** 2026-08-27

## Decision

App UI, TUI and CLI are clients of the canonical API.

Quantitative business logic lives behind the API in application/domain services.

## Consequences

- one semantic implementation serves all clients;
- clients may differ in UX, not quantitative rules;
- long-running operations use job semantics rather than long blocking client calls.
