# Scope: A16 General Quality Lifecycle v1 Foundation

## Objective

Materialize and implement `A16 - General quality lifecycle` so the repository
has accepted canonical authority and a bounded executable partition quality
lifecycle foundation under `quant_platform.data`.

This slice owns immutable target-bound quality assessment semantics, explicit
semantic supersession, deterministic current-assessment selection, exact
`pass -> valid`, `warn -> degraded`, `fail -> invalid` lifecycle mapping, and a
transactional catalog application seam for the current live sealed partition
revision.

## Baseline

```text
base main = 32e750fad6276ecd81f6870c1b41d4738176abc8
branch    = agent/issue-54-11
atom      = A16
owner     = Data Plane
```

At work start, local `origin/main` was verified as exactly
`32e750fad6276ecd81f6870c1b41d4738176abc8`.

## Accepted state after this slice

```text
A08  S13 certification                        FROZEN / COMPLETE
A09  S14 publication                          FROZEN / COMPLETE
A10  Backfill and repair                      OPEN_BLOCKING / MISSING
A11  Live trades acquisition                  OPEN_BLOCKING / MISSING
A16  General quality lifecycle                FROZEN / COMPLETE
B04  Non-contiguous coverage read             OPEN_BLOCKING / MISSING
```

`A16` is frozen by ADR-0029 and implemented in
`src/quant_platform/data/quality_lifecycle.py`.

## Included

- Create accepted ADR-0029 for General Quality Lifecycle v1.
- Update directly affected governance so A16 is no longer unresolved.
- Add deterministic semantic assessment signatures for applicable reports.
- Add explicit `metrics.supersedes_assessment_signature` graph validation.
- Select exactly one current authoritative assessment leaf.
- Map `pass/warn/fail` to `valid/degraded/invalid`.
- Add transactional current-live partition lifecycle application.
- Preserve S14 fail non-publication semantics.

## Excluded

- A10 repair trigger/scheduling/retry/revision semantics.
- A11 live overlap/cursor semantics.
- B04 partial/non-contiguous read semantics.
- dataset-level fan-out lifecycle mapping.
- new durable lifecycle-event table.
- generic quality framework, provider plugin framework or repair scheduler.

## Credited evidence

- S13 admits/seals one partition revision as `closed` and records immutable
  quality evidence separately.
- S14 selects evidence matching the current durable target and first-vertical
  profile/suite.
- Existing S14 publication semantics authorize pass/warn and refuse fail.
- `catalog.partitions` already supports `writing/closed/valid/degraded/invalid/superseded`.
- `catalog.quality_reports.metrics` carries structured JSON evidence.

## Acceptance

DONE means:

1. ADR-0029 is accepted and A16 is removed from unresolved governance.
2. `quant_platform.data.quality_lifecycle` exposes the A16 runtime foundation.
3. Current assessment selection is deterministic and never timestamp-based.
4. Explicit supersession is durable through quality-report metrics.
5. Missing, stale, ambiguous and invalid supersession evidence refuses.
6. `pass/warn/fail` maps exactly to `valid/degraded/invalid`.
7. Application affects only the current live sealed partition revision.
8. S14 fail remains non-publishable.
9. Dataset-level reports do not mutate partition lifecycle.
10. Repair/live policy remains downstream.

## Verification

Targeted checks for this slice:

```text
python tests/test_quality_lifecycle_v1.py
python tests/test_publication_eligibility_bridge_v1.py
python tests/test_publication_eligibility_bridge_v2.py
python tests/test_s13_revision_admission_v1.py
python tests/test_s13_same_revision_coverage_restatement_v1.py
python tests/test_package_boundaries_v1.py
python -m compileall -q src tests
python tools/check_markdown_links.py
git diff --check
```

## Downstream state

Unblocked by A16:

```text
A10  Backfill/repair may consume stable lifecycle quality facts after B04 and
     repair semantics are resolved.
```

Still blocked:

```text
B04  Non-contiguous coverage read semantics
A10  Repair trigger/precedence/retry/replacement semantics
A11  Live trades acquisition and cursor/overlap semantics
```
