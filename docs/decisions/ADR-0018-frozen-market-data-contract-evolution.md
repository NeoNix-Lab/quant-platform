# ADR-0018 — Frozen market-data contract evolution policy

**Status:** Accepted  
**Date:** 2026-08-28

## Context

The frozen v1 dataset-kind vocabulary does not cover every future L1, L3, candle, technical, context or custom representation.

## Decision

`trade-v1`, `dataset-manifest-v1` and `partition-manifest-v1` remain frozen. Future semantics must use explicit versioned contract evolution. If v1 cannot represent a capability cleanly, introduce a versioned v2 rather than silently changing v1 meaning.

## Consequences

Future L1/L3/candle materialization is contract-evolution work. This ADR does not design or implement v2 schemas.
