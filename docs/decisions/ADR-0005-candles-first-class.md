# ADR-0005 — Candles are first-class derived representations

**Status:** ACCEPTED  
**Date:** 2026-08-27

## Decision

Candles are reproducible first-class platform representations.

Candle semantics include source, interval, alignment, boundaries, partial/closed state and availability.

Candles may carry or join trade/L1/footprint/L2/L3-derived features at compatible grain.

## Consequences

Candlestick research and UI visualization do not require a separate data model.
Historical and incremental/live candle calculations must converge on identical final closed-candle semantics.
