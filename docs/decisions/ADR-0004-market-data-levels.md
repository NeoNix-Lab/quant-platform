# ADR-0004 — Distinguish market-data levels from feature levels

**Status:** ACCEPTED  
**Date:** 2026-08-27

## Decision

`L1`, `L2` and `L3` refer only to market-data semantics:

- L1: top-of-book;
- L2: aggregated depth;
- L3: order-level events.

First-stage derived values such as delta, imbalance and aggressive volume are called primitive/base derived features, not “L1 features”.

## Consequences

Raw/canonical market-data contracts and derived feature contracts remain separate.
