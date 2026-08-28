# ADR-0006 — Explicit temporal availability semantics

**Status:** ACCEPTED  
**Date:** 2026-08-27

## Decision

Temporal availability is a domain contract, not a backtest-only check.

The platform must be able to distinguish relevant times such as:

- event time;
- feature availability;
- signal availability;
- decision time;
- order submission;
- fill time.

## Invariant

No decision may use information that was not available by decision time.

## Consequences

Features, candles, labels, strategies, replay and live processing require temporal contract tests.
