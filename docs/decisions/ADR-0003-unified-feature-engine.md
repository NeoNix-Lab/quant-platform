# ADR-0003 — Unified Feature Engine

**Status:** ACCEPTED  
**Date:** 2026-08-27

## Decision

All derived market observables belong to one Feature Engine.

Provider families include:

- Trades
- L1
- Footprint
- L2
- L3
- Technical
- Context
- Custom

Order-flow is not a separate peer engine.

## Consequences

All feature providers must share FeatureDefinition, grain, availability, identity and lineage semantics.
