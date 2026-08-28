# ADR-0011 — Strategic RL and execution RL are different tasks

**Status:** ACCEPTED  
**Date:** 2026-08-27

## Decision

Strategic RL decides desired trading exposure/action.

Execution RL decides how to implement a desired trade/order objective.

They have distinct state, action and reward contracts.

## Consequences

Execution RL belongs within the execution-policy boundary, not as a substitute for strategic research semantics.
