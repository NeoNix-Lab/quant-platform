# ADR-0008 — Outcome Engine precedes labeling

**Status:** ACCEPTED  
**Date:** 2026-08-27

## Decision

The platform models future/path behavior as Outcomes before converting it into Labels.

Label families include:

- outcome-derived labels;
- policy-derived labels.

Policy-derived labels may quantify whether a trading decision was good under an explicit StrategySpec/ExecutionSpec.

## Consequences

Labels remain reproducible and their dependence on trading assumptions is explicit.
