# ADR-0010 — Supervised ML and RL are complementary

**Status:** ACCEPTED  
**Date:** 2026-08-27

## Decision

Supervised learning and RL are not mutually exclusive platform branches.

Supervised models commonly estimate outcomes/probabilities.

Strategic RL may consume raw features, portfolio state and supervised predictions to select actions/target exposure.

## Consequences

The architecture supports:

- Rules only;
- supervised model + rule policy;
- strategic RL;
- supervised predictions + strategic RL;
- other compatible combinations.
