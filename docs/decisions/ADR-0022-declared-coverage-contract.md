# ADR-0022 — Declared coverage is a separate durable contract

**Status:** Accepted

**Date:** 2026-08-29

## Context

The producer bridge from manifests to `catalog.partitions` is unimplemented, and
the semantics it would need are ambiguous in the current baseline.

`docs/contracts/MARKET_DATA_INGEST_CONTRACTS.md` §6 already separates *observed
event bounds* from *declared coverage* and leaves the population rule for
`catalog.partitions.ts_start` / `ts_end` explicitly open. `DATA_GATEWAY.md` §5
and §7 already consume those columns as declared half-open coverage, and
`src/quant_platform/data/catalog.py` prunes with them.

Three problems follow.

1. **Nothing durable carries declared coverage.** `partition-manifest-v1` has
   only `first_exchange_ts` / `last_exchange_ts`, which are observed event
   bounds and are null for a zero-row partition. `db/init/001_catalog.sql` opens
   with the principle that the database is rebuildable and that the truth lives
   in the manifests. For `ts_start` / `ts_end` that principle is currently
   false: the values would exist only in PostgreSQL.

2. **The frozen schema's prose contradicts the newer contract.**
   `partition-manifest-v1.json` calls the observed bounds "copertura", states
   that "la copertura temporale REALE resta descritta da
   first/last_exchange_ts", and says gaps and overlaps between partitions are
   detected from them. Read literally, that instructs a future producer to treat
   distance between events as a data gap.

3. **Several required cases have no representation at all**: complete coverage
   with zero events, a collector interruption that no event pattern reveals, a
   sequence discontinuity as distinct from silence, and a gap for an interval
   where nothing was ever materialized.

Left unresolved, the bridge implementation would have to invent coverage
semantics at runtime.

## Decision

Freeze declared coverage as a **separately versioned durable contract**,
`coverage-manifest-v1`, keyed to the natural **dataset** identity. One document
records one acquisition or reconciliation operation: its intent, its versioned
source semantics and mapping, and one or more interval assertions each carrying
a status and source-specific evidence. Only `status: complete` declares
coverage, and it attributes to exactly one natural partition identity.

`partition-manifest-v1` is **not** modified, in rules or in bytes. Its stale
wording is corrected by an errata table in
[DECLARED_COVERAGE.md](../contracts/DECLARED_COVERAGE.md) §7 rather than by
editing a frozen artifact.

The full semantics, the fifteen invariants and the reconstruction rule are in
[DECLARED_COVERAGE.md](../contracts/DECLARED_COVERAGE.md).

## Alternatives considered

**A — `partition-manifest-v2` with declared-coverage fields.** Rejected. It
fails three required cases structurally rather than inconveniently. A partition
manifest exists only once a partition is materialized, so a known gap over an
interval where nothing was written has nowhere to live. It has one interval slot
per partition, so an interrupted session inside one partition cannot be stated
truthfully. And because the document is keyed by `(dataset, partition_key,
revision)` and carries the content `sha256`, revising coverage after a repair
that changes no bytes would force a spurious revision `N+1` and supersession.
It would also invalidate every existing manifest and fixture for a change the
sealed content did not require.

**B — companion coverage sidecar per natural partition identity.** Rejected. It
solves the multiple-intervals-per-partition problem but keeps the other two:
still nothing to attach to when no partition exists, and an operation spanning
partitions must duplicate its intent and evidence per file. Amending a sidecar
in place after a repair would also make the durable evidence mutable, with no
supersession chain — the opposite of what provenance needs.

**C — a separately versioned dataset-level contract.** Chosen. Coverage is a
claim about an interval, so the artifact is keyed by interval and dataset, not
by file. It represents every required case, leaves `partition-manifest-v1`
untouched, keeps sealed content and coverage on independent lifecycles, and
gives supersession an explicit chain.

Within C, one document per *operation* was chosen over one document per
*assertion*: an interrupted live session must be able to state
`complete / known_gap / complete` as one truthful record, and intent, source
semantics and provenance are properties of the operation, not of each interval.

**Catalog-only storage** was excluded by the mandate and is independently wrong:
it would contradict the rebuildability principle the catalog DDL already
states.

## Consequences

The future bridge is deterministic. It folds `complete` assertions per partition
and publishes only a single contiguous interval; it never inspects event
spacing, observed bounds or `partition_key` to obtain coverage.

Two failure modes become loud instead of silent. Copying observed bounds into
`ts_start` / `ts_end` is rejected by the containment invariant, because
half-open coverage always places `last_exchange_ts == ts_end` outside the
interval. And a partition whose attributed coverage is non-contiguous is
reported as unpublishable rather than published with its internal gap declared
covered.

That second case is a real constraint the producer inherits, and a
cross-boundary audit against the merged Consumer API baseline sharpened it.
Marking the partition `degraded` does **not** make the outer span safe:
`state` decides which partitions are selected, never how much of their span
counts, so under `VALID_CLOSED_AND_DEGRADED` the gateway reports
`coverage_complete = true` across a known gap and a request aimed at the gap
returns a successful empty result. Publishing the outer span is therefore
forbidden in every lifecycle state. Re-keying so each published partition is
contiguous is permitted but never mandated, because letting transport accidents
choose partition keys is an implementation leak. A catalog coverage relation is
the natural future evolution and is not available in v1. This ADR makes the
situation detectable and bounded, not resolved.

Producers gain an obligation: every eligible partition needs a coverage manifest
before it can be published. There is no historical data to migrate, because no
partition has been published through a bridge that does not yet exist.

Fixing the reference validator's timestamp parser to keep nanoseconds was
required by this contract: `datetime.fromisoformat` truncates below the
microsecond, and observed event bounds (`first_exchange_ts`/`last_exchange_ts`,
frozen at nine fractional digits) must compare exactly for the I8 containment
check to be trustworthy. The validator now matches `epoch_ns` throughout.

This ADR does not implement the bridge, a collector, live ingest, storage
tiering, or any change to DataGateway.

## Addendum — independent review hardening

An independent review of this candidate raised four blockers and five
important findings, all accepted and closed without reopening the core
decision above. In summary:

- the supersession graph (`coverage_id`/`supersedes`) is now validated for
  self-reference, cycles, branching and dangling targets, fail-closed: any
  structural defect makes the whole reconstruction call publish nothing,
  rather than silently dropping the affected documents;
- a `complete` assertion must now resolve to exactly one existing, eligible
  partition manifest, and a `partition_key` may have at most one live
  (non-`superseded`) revision among the manifests supplied;
- declared coverage boundaries (not observed event timestamps, which stay
  nanosecond-capable and frozen) are capped at microsecond precision, matching
  what `catalog.partitions.ts_start`/`ts_end` — PostgreSQL `timestamptz` — can
  hold exactly, closing a mismatch the original nanosecond-parser fix had left
  open for the declared side;
- `row_count == 0` now mechanically requires null observed bounds, closing a
  direction the frozen schema's own conditional leaves open;
- every assertion carries a document-scoped `assertion_id`, and
  `reconstruct_catalog_coverage()` refuses mixed-`DatasetIdentity` input
  outright.

Full rationale is in
[DECLARED_COVERAGE.md](../contracts/DECLARED_COVERAGE.md) invariants I1, I3,
I10 and I13–I15, and executable proof is in
[test_coverage_lineage_and_reconstruction.py](../../tests/test_coverage_lineage_and_reconstruction.py).
