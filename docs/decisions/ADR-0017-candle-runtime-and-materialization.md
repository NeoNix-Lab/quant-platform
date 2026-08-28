# ADR-0017 — Candle runtime and materialization model

**Status:** Accepted  
**Date:** 2026-08-28

## Context

Candles must support both incremental live operation and reproducible historical data.

## Decision

`CandleDefinition` owns reproducible semantics. A `PARTIAL` candle is mutable and incremental and is not sealed. A `CLOSED` candle is immutable after closure. A reusable closed-candle series may be persisted as a canonical derived dataset with catalog, partition and lineage semantics.

## Consequences

Candles are first-class representations, not merely features. Candle code and schemas remain future work.
