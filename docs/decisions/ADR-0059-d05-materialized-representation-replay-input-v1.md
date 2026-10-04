# ADR-0059 — D05 materialized representation and replay input v1

**Status:** ACCEPTED
**Date:** 2026-10-04

## Context

H05 replay currently consumes ordered `TradeRecord` values through
`DataGateway.scan()`.  Candle v1 already defines deterministic CLOSED records,
their definition identity, exact source support, source-finalization evidence,
and the causal floor `bucket_end`; E04 separately defines FINAL-only feature
artifact evidence.  Neither contract is a durable representation artifact nor
a replay input profile.

## Decision

### 1. D05 representation artifact identity

A future `RepresentationArtifact` is a FINAL-only, immutable evidence envelope
for one materialized representation.  Its semantic identity must bind:

- representation definition identity and canonical definition payload;
- source dataset identity, source representation/schema/ordering, natural
  partitions, manifest/content digests, and source request/result evidence;
- exact declared output support and the required source support used to create
  it;
- output partition identities plus their content digests; and
- implementation identity and output record schema.

Physical locators, catalog surrogate IDs, transfer paths, and materialization
wall-clock time are not identity-bearing.  Relocation cannot change identity.
This is deliberately parallel to E04's `FeatureArtifact`, not an alias for it:
a representation is an owned reusable market-data view, while a feature
artifact binds feature-definition/output-contract evidence.

### 2. Candle support and availability

For candle v1, each materialized row is a CLOSED `[bucket_start,bucket_end)`
bucket.  Its causal availability is exactly `bucket_end`; observed availability
is the attributable source-finalization time and may not precede that floor.
PARTIAL rows and a bucket with incomplete required source support are never
materialized D05 inputs.  The artifact exposes its declared output support and
the exact required source support separately; availability timestamps do not
stand in for either support shape.

### 3. Replay input profiles and comparability

A future replay spec must declare an input profile and bind the representation
artifact identity.  It may consume only FINAL rows whose causal availability is
at or before its decision time.  Precomputed feature inputs additionally bind
their E04 artifact identity, representation/source support, and each feature
observation's causal availability.

Tick replay and representation replay are **not interchangeable by default**.
They are comparable only under a future declared bar-close profile that
constrains decisions to the same CLOSED-bar availability points and proves the
same order/fill/ledger semantics.  An intrabar tick strategy, a strategy using
partial candles, or a profile with different decision/fill timing is an
intentionally different experiment and cannot claim tick-level equivalence.

### 4. DataGateway and implementation boundary

`DataGateway` remains the canonical source/catalog-resolution seam.  D05 does
not permit replay to open representation storage paths directly.  A later
access/application slice must resolve a declared representation artifact to
its records and provenance, then feed the future replay profile.  No
two-resolution runtime is implemented by this ADR.

### 5. Required follow-up proof

Implementation must prove that materializing an exact finalized source slice
produces the same CandleDefinition v1 records, source evidence, coverage and
result identity as the existing streaming historical computation.  A bar-close
replay profile must additionally prove equivalence against a streaming candle
adapter on the same support, and must fail closed for a partial, unfinalized,
or causally future row/feature.  Those tests are not evidence for intrabar
tick equivalence.

## Consequences

- Fast representation-based research becomes possible without weakening
  source provenance or causal availability.
- D05, representation access, and a bar-close replay profile are separate
  implementation slices; no future consumer may infer them from a file path.

## Out of scope

- D05 runtime, two-resolution replay, Omega screening, paper/live trading,
  and any change to existing tick replay semantics.

## Acceptance evidence

`tests/test_candle_definition_v1.py` covers deterministic CLOSED candle
definition, support, finalization, and availability behavior; `tests/test_feature_artifact_v1.py`
covers FINAL-only feature-artifact evidence.  Neither is a materialized D05 or
representation-replay proof, which remains required above.

## Related

Parent tracking: #232. Closes issue #236.
