# ADR-0001 — Authoritative product codebase

**Status:** ACCEPTED  
**Date:** 2026-08-27

## Context

The project has valuable functionality and documentation in a legacy quantitative codebase, while the newer market-platform codebase contains the canonical data-plane direction.

Backward compatibility is not a product requirement.

## Decision

The current canonical platform repository is the authoritative product codebase.

Legacy repositories are read-only reference material.

Legacy code may enter the canonical runtime only through capability-level semantic review and adoption.

## Consequences

- no architecture is preserved merely for compatibility;
- no wholesale legacy module copy;
- legacy tests/algorithms may be reused;
- conflicting legacy documentation is reported rather than silently merged.
