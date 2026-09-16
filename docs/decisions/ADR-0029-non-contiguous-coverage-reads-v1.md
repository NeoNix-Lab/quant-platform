# ADR-0029 - Non-contiguous coverage reads v1

**Status:** ACCEPTED

**Date:** 2026-09-16

## Context

B02 and B03 established the canonical DataGateway boundary, bounded scan
surface, deterministic ordering and result provenance for historical canonical
market-data reads. ADR-0022 established declared coverage as authoritative
support separate from observed event bounds.

The accepted `STRICT` coverage behavior remains correct for consumers that
require complete support. The DG-B repair/shared path also needs a way to read
the explicitly covered islands of a request while carrying exact gap facts to
downstream repair policy. That behavior must be an explicit request semantic,
not an inferred fallback and not a repair policy.

Credited compatibility evidence:

- `DataRequest` already includes `coverage_policy` in normalized request
  identity.
- DataGateway preparation already computes eligible coverage and gaps from
  declared/catalog coverage.
- DataGateway metadata and result identity already carry eligible coverage,
  coverage gaps, coverage completeness, returned bounds and row count.
- DataScan already enforces one canonical global ordering across selected
  partitions.
- ADR-0022 already defines declared half-open coverage, adjacency and gap
  projection independently from event silence.

## Decision

Adopt B04 non-contiguous coverage reads v1 as an additive DataGateway contract
extension.

DataGateway supports exactly two request-level coverage policies:

```text
STRICT
ALLOW_PARTIAL
```

`STRICT` remains the default. It requires the complete requested interval to be
covered by eligible authoritative coverage. Zero eligible coverage, leading
gaps, trailing gaps and internal gaps all raise `NoCoverage`.

`ALLOW_PARTIAL` is explicit opt-in. It succeeds only when at least one
non-empty eligible authoritative coverage interval intersects the requested
interval and all other dataset, schema, manifest, catalog, lifecycle and
ordering evidence is valid. It may return covered islands separated by gaps.

For every successful read:

- `requested_interval` remains the original request interval;
- `eligible_coverage` is the normalized, sorted, merged union of eligible
  declared coverage intersected with the request;
- `coverage_gaps` is the exact complement of that union inside the request;
- `coverage_complete` is true iff `coverage_gaps` is empty;
- rows come only from eligible selected partitions and declared covered support
  intersecting the request;
- half-open adjacency where `A.end == B.start` is contiguous support, not a
  gap.

`coverage_policy` participates in normalized `DataRequest` identity. Otherwise
identical `STRICT` and `ALLOW_PARTIAL` requests have distinct
`request_identity` values.

B04 reuses the existing B03 result identity/provenance model. Coverage policy,
eligible coverage, coverage gaps, coverage completeness, returned bounds and
row count remain part of the support/result payload. There is no second
partial-read identity scheme.

`ALLOW_PARTIAL` relaxes only coverage completeness. It does not relax catalog
or manifest consistency, schema support, lifecycle-state selection, canonical
ordering, overlap conflict refusal or finite scan completion semantics.
Unexplained overlap between distinct eligible live partition identities remains
`CatalogConflict` under both policies.

No interpolation, synthetic continuity, sentinel rows, zero-fill rows,
carry-forward rows or event-spacing inference is permitted. Dataset-level,
repair, live, source reacquisition, duplicate resolution, pagination and client
presentation policy remain downstream.

## Runtime materialization

The accepted runtime foundation is implemented under `quant_platform.access`
as:

- canonical `CoveragePolicy` values `STRICT` and `ALLOW_PARTIAL`;
- normalized request validation with `STRICT` as the default;
- stable request serialization using the canonical policy token;
- existing DataGateway coverage projection reused for both policies;
- explicit zero-support `NoCoverage` refusal for non-empty requests under both
  policies;
- strict gap refusal preserved for `STRICT`;
- partial success for non-empty eligible support under `ALLOW_PARTIAL`;
- row validation against partition declared coverage;
- unchanged result metadata/fingerprint construction.

The implementation intentionally introduces no repair scheduler, historical/live
merge, cursor, source reacquisition path, generic planner, provider plugin,
materialization mutation or new provenance identity system.

## Consequences

- B04 is frozen and complete for the bounded v1 DataGateway runtime extension.
- Consumers that need complete support keep using the default `STRICT` policy.
- A10 repair can later consume exact requested support, eligible support, gaps
  and returned-row facts without defining quality or lifecycle truth here.
- A11/B06 live cursor and historical/live merge behavior remain unresolved and
  downstream of their own authorities.
- B05 DatasetSnapshot remains deferred until durable replay need exists.

## Acceptance evidence

Executable proof is in `tests/test_b04_non_contiguous_coverage_reads_v1.py`.

The proof covers default `STRICT`, distinct request identities for `STRICT` and
`ALLOW_PARTIAL`, full contiguous coverage under both policies, internal,
leading and trailing gaps under `ALLOW_PARTIAL`, strict gap refusal,
covered-support zero-row success, zero-support refusal, half-open adjacency,
overlap conflict refusal under both policies, result identity sensitivity to
gap/support shape and absent final metadata for aborted scans.
