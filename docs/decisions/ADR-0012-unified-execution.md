# ADR-0012 — Single canonical Execution Engine

**Status:** ACCEPTED  
**Date:** 2026-08-27

## Decision

Rules, supervised ML and RL do not receive separate backtest/execution engines.

They converge to common execution semantics.

## Consequences

Fees, slippage, latency, orders, fills and portfolio accounting are implemented once and reused across policy families.
