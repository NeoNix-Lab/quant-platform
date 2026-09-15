# Scope: D06 FootprintDefinition v1 Representation Foundation

## Objective

Materialize and implement `D06 - Footprint representation` so the repository
has accepted canonical authority and a bounded executable historical
`FootprintDefinition v1` foundation under `quant_platform.representation`.

This slice owns only Representation-level Footprint meaning: definition
identity, fixed bucket support, exact tick-grid validation, sparse price-level
aggregation, explicit aggression evidence, finalized historical result binding
and provenance.

## Baseline

```text
base main = 32e750fad6276ecd81f6870c1b41d4738176abc8
branch    = agent/issue-52-9
atom      = D06
owner     = Representation
```

At work start, local `origin/main` was verified as exactly
`32e750fad6276ecd81f6870c1b41d4738176abc8`.

## Accepted state after this slice

```text
D01  Representation identity                 RESOLVED / PARTIAL
D02  CandleDefinition v1                      FROZEN / COMPLETE
D03  Historical Candle computation            FROZEN / MISSING
D04  Incremental/live Candle computation      FROZEN / MISSING
D05  Candle materialization identity          OPEN_BLOCKING / MISSING
D06  Footprint representation                 FROZEN / COMPLETE
E02  FeatureDefinition v1                     FROZEN / COMPLETE
E04  FeatureArtifact/materialization          OPEN_BLOCKING / MISSING
E05  H01 pure imbalance kernel                RESOLVED / MISSING in governance; pure kernel present
E06  H01 canonical integration                OPEN_BLOCKING / MISSING
```

`D06` is frozen by ADR-0027 and implemented as immutable typed values and pure
historical aggregation functions in
`src/quant_platform/representation/footprints.py`.

## Included

- Create accepted ADR-0027 for FootprintDefinition v1.
- Update directly affected governance so D06 is no longer unresolved.
- Add immutable `FootprintDefinitionV1` with deterministic semantic payload
  and content-derived identity.
- Add exact UTC epoch-aligned half-open bucket support selection.
- Add exact zero-origin tick-grid validation with no rounding or tolerance.
- Add deterministic sparse level aggregation from canonical `TradeRecord`
  input into buy/sell volume by explicit aggressor side.
- Refuse unknown, missing or unsupported aggression fail-closed.
- Add finalized historical result, bucket, level, coverage and source evidence
  values that distinguish covered empty buckets from insufficient support.
- Preserve E05 pure H01 kernel semantics.

## Excluded

- FeatureDefinition runtime/identity changes.
- FeatureArtifact identity, materialization, caching or durable persistence.
- H01 canonical orchestration/integration/adapters.
- DataGateway/catalog/Application redesign or mutation.
- Live incremental/PARTIAL Footprint topology.
- Durable Footprint file/schema publication or catalog mutation.
- Non-zero-origin grid semantics.
- Generic representation framework, provider registry or feature DAG.

## Credited evidence

- Producer-Consumer Conformity `Contract Freeze Gate` and `Conformity
  Implementation Gate` remain PASSED under ADR-0023 and are not reopened by
  this slice.
- `trade-v1` keeps exact price/size strings and explicit `aggressor_side`.
- `CandleDefinitionV1` demonstrates deterministic Representation definition
  identity, aligned half-open support and availability evidence separation.
- `src/quant_platform/features/imbalance.py` is a pure H01 kernel over
  caller-supplied integer price-level rows and does not own Footprint
  construction.
- `tests/test_feature_imbalance_v1.py` proves duplicate-level refusal,
  integer-grid adjacency, sparse-gap stack breaking and bar separation.
- `db/init/001_catalog.sql` distinguishes `canonical/footprint` from
  `features/footprint_microstructure`.

## Acceptance

DONE means:

1. ADR-0027 is accepted and D06 is removed from unresolved governance.
2. `quant_platform.representation` exposes the FootprintDefinition v1 runtime
   model.
3. Definition identity includes semantic duration/tick/source/grid/bucket
   semantics and excludes runtime binding/materialization context.
4. Historical support is UTC epoch-aligned, half-open and FINAL-only.
5. Tick-grid validation is exact and zero-origin with no binary-float
   tolerance.
6. Same-level trades aggregate deterministically and exactly.
7. Missing/unknown/unsupported aggressor side fails closed.
8. Missing grid levels remain absent and realized levels are strictly ordered.
9. Covered zero-trade buckets are explicit empty buckets, distinct from
   insufficient support.
10. Source/support/provenance binding is explicit without durable artifact
    semantics.
11. E05 kernel behavior remains unchanged and compatible.
12. Live/PARTIAL topology remains deferred.

## Verification

Targeted checks for this slice:

```text
python tests/test_historical_footprints_v1.py
python tests/test_feature_imbalance_v1.py
python tests/test_candle_definition_v1.py
python tests/test_package_boundaries_v1.py
python -m compileall -q src tests
python tools/check_markdown_links.py
git diff --check
```

## Downstream state

Unblocked by D06:

```text
E06  H01 canonical integration can bind to canonical Footprint semantics after E04
```

Still blocked:

```text
E04  FeatureArtifact/materialization
E06  H01 canonical integration
D05  Candle materialization identity (independent branch)
Live/PARTIAL Footprint topology
Durable Representation artifact infrastructure
```
