# ADR-0014 — Unified experiment provenance

**Status:** ACCEPTED  
**Date:** 2026-08-27

## Decision

Study, Trial, Run and Artifact form one coherent experiment/provenance model.

A reproducible result must identify its:

- data;
- feature definitions;
- research/label semantics;
- validation;
- policy/strategy;
- execution;
- code;
- relevant environment;
- outputs/metrics.

## Consequences

Subsystem-specific run identities and hidden in-memory artifact identities are not canonical.
