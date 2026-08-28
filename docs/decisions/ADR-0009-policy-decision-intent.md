# ADR-0009 — Learner-agnostic DecisionIntent boundary

**Status:** ACCEPTED  
**Date:** 2026-08-27

## Decision

Rules, supervised-assisted policies and strategic RL policies converge on a common DecisionIntent contract.

Execution consumes DecisionIntent without needing to know the learner/policy family that generated it.

## Consequences

Backtest and live execution semantics can be shared across strategy families.
