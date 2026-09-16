# Scope: B04 Non-contiguous Coverage Reads v1

## Objective

Materialize and implement `B04 - Non-contiguous coverage read` so the
DataGateway contract has accepted authority for explicit partial historical
reads and the runtime supports an additive `ALLOW_PARTIAL` coverage policy
inside `quant_platform.access`.

This slice owns only historical DataGateway coverage-read semantics:
request-level `STRICT | ALLOW_PARTIAL`, exact declared support/gap projection,
zero-support refusal, unchanged canonical ordering/conflict behavior and
existing result identity/provenance reuse.

## Baseline

```text
base main = 32e750fad6276ecd81f6870c1b41d4738176abc8
branch    = agent/issue-55-12
atom      = B04
owner     = Data Access
```

At work start, local `origin/main` was verified as exactly
`32e750fad6276ecd81f6870c1b41d4738176abc8`.

## Accepted state after this slice

```text
A04  Declared coverage                       FROZEN / COMPLETE
A09  S14 publication                         FROZEN / COMPLETE
A10  Backfill and repair                     OPEN_BLOCKING / MISSING
A11  Live trades acquisition                 OPEN_BLOCKING / MISSING
A16  General quality lifecycle               OPEN_BLOCKING / PARTIAL
B02  Bounded historical scan                 FROZEN / COMPLETE
B03  Result identity/provenance              FROZEN / COMPLETE
B04  Non-contiguous coverage read            FROZEN / COMPLETE
B05  Durable DatasetSnapshot                 OPEN_DEFERABLE / MISSING
B06  Live access/cursor                      OPEN_BLOCKING / MISSING
```

`B04` is frozen by ADR-0029 and implemented as an explicit DataGateway
coverage policy using the existing coverage/provenance model.

## Included

- Update `docs/contracts/DATA_GATEWAY.md` with accepted B04 coverage policy
  semantics.
- Create accepted ADR-0029 for non-contiguous coverage reads v1.
- Update directly affected governance so B04 is no longer unresolved.
- Add canonical `CoveragePolicy` values with `STRICT` as the default.
- Preserve `STRICT` gap refusal exactly.
- Permit explicit `ALLOW_PARTIAL` success when non-empty eligible declared
  support intersects the request.
- Preserve zero-support `NoCoverage` refusal.
- Preserve existing result metadata/fingerprint semantics and global ordering.
- Prove exact leading, trailing and internal gap metadata without synthetic
  rows.

## Excluded

- A10 repair trigger, retry, replacement revision or backfill policy.
- A16 general quality lifecycle implementation.
- A11/B06 live cursor, historical/live merge or stream-resume behavior.
- Source acquisition, duplicate resolution or interpolation/filling.
- DatasetSnapshot public API.
- Materialization/storage mutation.
- Generic query planner, provider plugin or second provenance identity system.

## Credited evidence

The completed Producer–Consumer Conformity cycle remains credited under
ADR-0023: **Contract Freeze Gate = PASSED** and **Conformity Implementation
Gate = PASSED**. This slice does not reopen either gate.

- B02 bounded `DataScan` and B03 provenance/result identity semantics are
  already accepted.
- ADR-0022 declared coverage remains authoritative and distinct from observed
  row bounds.
- DataGateway already computes eligible coverage and gaps from catalog
  coverage and exposes them in metadata.
- Existing overlap/conflict and canonical ordering behavior remain fail-closed
  and are reused.

## Acceptance

DONE means:

1. `STRICT` remains the default and preserves existing complete-coverage reads.
2. Explicit `ALLOW_PARTIAL` produces a distinct request identity.
3. Zero eligible support remains `NoCoverage`.
4. Covered zero-event support remains a successful zero-row result.
5. Leading, trailing and internal gaps are exact in existing metadata.
6. Returned rows come only from eligible declared support with no interpolation
   or synthetic continuity.
7. Canonical global ordering and overlap conflict behavior are unchanged.
8. Result identity/provenance distinguish support shape without a new identity
   system.
9. Aborted scans still have no final result metadata.
10. Repair, live and client policy remain downstream.

## Verification

Targeted checks for this slice:

```text
python tests/test_b04_non_contiguous_coverage_reads_v1.py
python tests/test_data_gateway.py
python tests/test_bounded_datagateway_read_v1.py
python tests/test_declared_coverage_semantics.py
python -m compileall -q src tests
python tools/check_markdown_links.py
git diff --check
```

## Downstream state

Unblocked by B04:

```text
A10 repair policy may consume explicit requested support, eligible support,
exact gaps and returned-row facts after its other prerequisites are satisfied.
```

Still blocked:

```text
A16  General quality lifecycle beyond the accepted first vertical
A10  Repair trigger/retry/revision semantics
A11  Live acquisition overlap/duplicate/restart semantics
B06  Live access/cursor semantics
B05  Durable DatasetSnapshot shape
```
