# ADR-0033 - Backfill / repair v1

**Status:** ACCEPTED

**Date:** 2026-09-17

## Context

DG-B's repair branch needed a bounded runtime for reconciling explicit
defects (coverage gaps, invalid revisions) against an already-published
canonical partition, without duplicating A16 quality-lifecycle or A09/S14
publication-eligibility authority, and without silently racing concurrent
ordinary S13/S14/A16 activity on the same dataset.

Credited compatibility evidence:

- A16 (`quality_lifecycle`) and A09/S14 (`publication_eligibility_catalog`)
  already own transaction-scoped quality/eligibility decisions and expose
  reusable seams.
- B04 (ADR-0029) already reports requested support, eligible support, exact
  gaps and returned rows as caller-suppliable `CoverageInterval` evidence,
  without deciding repair triggering itself.
- Package-boundary rule: `quant_platform.data` must not import
  `quant_platform.access`, so A10 consumes B04 gap/coverage evidence only as
  already-frozen `CoverageInterval` values the caller supplies, never by
  calling `access` itself.

## Decision

Adopt A10 backfill/repair v1 under `quant_platform.data.repair` as
evidence-driven reconciliation over explicit defects only -- never a
generic self-healing or scheduling system.

The bounded runtime foundation is: deterministic repair-intent identity
(`RepairIntent`, `PredecessorRef`, `CoverageGapTrigger`,
`InvalidRevisionTrigger`), immutable isolated candidate attempts
(`CandidateAttempt`, `CandidateStaging`, refusing concurrent clobbering via
`CandidateStagingConflict`), and one dedicated atomic compare-and-cutover
transaction (`RepairCutoverCatalog`) that reuses the existing A16 and A09/S14
transaction-scoped seams rather than re-implementing quality or eligibility
decisions.

Explicit outcome vocabulary (no silent partial state):

```text
REPAIR_REQUIRED
CANDIDATE_PENDING
CONVERGED
ALREADY_SATISFIED
FAILED
STALE_CONFLICT
```

### Cutover cannot reuse the ordinary S13 admission path

`CatalogPublicationWriter` supersedes the predecessor and commits before
quality/publication evidence is verified -- wrong for replacement repair. A10
instead supersedes the predecessor and admits the candidate as `closed`
inside its own transaction, then calls the A16 and S14 transaction-scoped
seams against that same connection, and only commits if both accept the
exact final revision -- so a failed candidate never leaves a half cutover
and the predecessor stays authoritative.

### Lock ordering matches S14's own order

All relevant dataset locks, child and lineage parents (sorted), before the
natural-partition topology lock -- applied throughout, including the
pre-mutation re-authorization step -- so a concurrent ordinary S13/S14/A16
call sharing a dataset can only ever block on this transaction, never
deadlock against it.

### Convergence is recorded durably, never inferred from a hash match

`ConvergenceProvenance`, written inside the same commit into
`catalog.repair_convergence`. `ALREADY_SATISFIED` is decided by looking up
that row for the live partition and confirming it names this exact
`repair_intent_id`/`candidate_id` pair -- never by comparing content hashes
alone, because a coincidental hash match between two differently-evidenced
candidates must never be treated as the same repair.

## Runtime materialization

Implemented under `quant_platform.data.repair`, reusing
`quant_platform.data.coverage.reconstruct_catalog_coverage`,
`quant_platform.data.quality_lifecycle` and
`quant_platform.data.publication_eligibility_catalog` rather than duplicating
their decisions. Candidate acceptance-input evidence
(`compute_quality_evidence_id`, `compute_coverage_evidence_id`) binds
cryptographically to `check_suite`/`expected_profile`/coverage bounds so the
same report content cannot be re-filed under a different suite or
re-evaluated under a different profile while `candidate_id` stays unchanged.

The implementation intentionally introduces no acquisition/download owner,
scheduler, A11 live-cursor semantics or deletion authority.

## Consequences

- A10 is frozen and complete for the bounded v1 repair runtime: coverage-gap
  and invalid-revision triggers, isolated candidates, and one atomic
  compare-and-cutover transaction.
- Historical repair does not activate live-cursor semantics; A11/B06 remain
  separately unresolved.
- **Accepted known limitations** (issue #60 amendment, 2026-09-17):
  - coverage-trigger *re-verification* (independently re-confirming a gap
    is real at cutover time, rather than trusting the caller-supplied B04
    evidence that triggered the repair) is architecturally unsatisfiable
    within A10's own package boundary, since `quant_platform.data` cannot
    import `quant_platform.access`, where B04 lives. This mirrors the
    attributable-evidence principle (ADR-0032): A10 verifies internal
    consistency of the caller-supplied gap evidence, not the gap's
    independent truth;
  - candidate identity binding for `coverage_start`/`coverage_end` and full
    provenance persistence remain partial, tracked for a future pass rather
    than blocking this foundation.

## Acceptance evidence

Executable proof is in `tests/test_data_repair_v1.py` and
`tests/integration_a10_repair_postgres.py`.
