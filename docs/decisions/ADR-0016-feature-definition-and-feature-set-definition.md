# ADR-0016 — FeatureDefinition and FeatureSetDefinition are distinct identities

**Status:** Accepted  
**Date:** 2026-08-28

## Context

One derived observable and a versioned bundle/materialization definition have different identity and lifecycle needs. The catalog already contains `feature_set_definitions`.

## Decision

`FeatureDefinition` identifies one semantic observable such as `delta@1` or `vwap@1`. `FeatureSetDefinition` identifies a versioned bundle containing one or more feature definitions and its materialization semantics. The existing catalog foundation is retained.

## Consequences

Feature identity, feature-set identity and materialized artifact identity remain distinct. No feature runtime is implemented by this ADR.
