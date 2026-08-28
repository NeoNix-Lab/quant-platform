# ADR-0013 — Historical replay and live share strategy semantics

**Status:** ACCEPTED  
**Date:** 2026-08-27

## Decision

Historical, paper/shadow and live runtimes should differ primarily in market-event source and operational constraints.

Strategy/DecisionIntent/Execution contracts are shared.

## Consequences

Historical-first development must still model incremental availability and event ordering needed by live operation.
