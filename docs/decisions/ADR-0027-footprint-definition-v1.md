# ADR-0027 - FootprintDefinition v1 representation foundation

**Status:** ACCEPTED

**Date:** 2026-09-15

## Context

The canonical H01 path needs a Representation-owned price-level trade
aggregation before Feature integration can bind to it. E05 already provides a
pure imbalance kernel over caller-supplied `(bar_id, level_index, buy_volume,
sell_volume)` rows, and E02 already provides FeatureDefinition v1 semantics.
Neither owns Footprint formation, tick-grid validation, bucket support or
source aggression evidence.

D06 resolves the minimum historical Footprint v1 foundation needed before E06
without absorbing FeatureArtifact persistence, H01 thresholds, DataGateway
orchestration, live/PARTIAL topology or durable representation materialization.

Credited compatibility evidence:

- `trade-v1` supplies exact string price and size values, canonical
  `exchange_ts`, and explicit `aggressor_side`, while allowing `unknown`.
- `CandleDefinitionV1` establishes the Representation pattern for
  deterministic definition identity, UTC epoch-aligned half-open buckets,
  causal availability floors and nullable observed availability evidence.
- `src/quant_platform/features/imbalance.py` remains a pure H01 kernel and
  deliberately does not define Footprint construction or tick-grid semantics.
- The catalog already distinguishes `canonical/footprint` from
  `features/footprint_microstructure`.

## Decision

Adopt `FootprintDefinition v1` as a canonical Representation definition.
Footprint is not a FeatureDefinition and contains no H01 thresholds,
imbalance labels, feature artifact identity or strategy semantics.

### Definition identity

`FootprintDefinitionV1` identity is a SHA-256 digest of canonical UTF-8 JSON
containing only semantic representation parameters:

- version `1`;
- source contract `trades@1` / `trade-v1`;
- event-time field `exchange_ts`;
- fixed positive duration in nanoseconds;
- UTC Unix epoch alignment;
- half-open bucket support `[bucket_start,bucket_end)`;
- positive exact-decimal `tick_size`;
- zero-origin exact tick grid;
- sparse price-level aggregation, ordering and finality semantics;
- explicit aggression requirements and refusal policy.

Venue, instrument, dataset/catalog UUIDs, physical paths, query ranges,
process IDs, runtime locators, implementation revision and materialization
locations are excluded from pure definition identity. A concrete Footprint
binding records those facts as provenance, not as definition meaning.

### Temporal support and finality

Historical Footprint v1 uses one fixed positive duration. Buckets are aligned
to the UTC Unix epoch and use half-open support. A trade belongs to exactly one
bucket by `exchange_ts`. Bucket identity includes the aligned support interval.

The D06 runtime exposes finalized historical results only. Final results
require complete authoritative source support and source finalization evidence
for every selected bucket. The causal availability floor is `bucket_end`;
observed source availability/finalization time remains nullable unless
evidenced. A future live/PARTIAL topology must converge to the same finalized
result for the same definition and finalized source evidence, but is not
defined here.

Complete source coverage with zero trades produces a finalized empty bucket
with no level rows. Missing source coverage is not the same condition and
refuses a FINAL result.

### Tick grid and levels

The v1 grid is zero-origin:

```text
level_index = price / tick_size
```

`tick_size` is a positive exact-decimal semantic parameter. A source price is
valid only when the quotient is an exact integer. No rounding, snapping,
epsilon tolerance or nearest-tick repair is permitted. The canonical level
coordinate is integer `level_index`; canonical price is exactly
`level_index * tick_size`.

Within one bucket and level index, trades aggregate into exactly one semantic
level:

- `buy_volume` is the exact sum of source sizes whose aggressor side is `buy`;
- `sell_volume` is the exact sum of source sizes whose aggressor side is
  `sell`;
- both volumes are non-negative;
- at least one side must be strictly positive for a level row to exist.

Realized levels are ordered by strictly increasing integer `level_index`.
Missing grid levels are absent. No zero-volume rows are synthesized for sparse
gaps, and more than one realized row for the same bucket/level is malformed.

### Aggressor side

`trade-v1` value `aggressor_side == "unknown"` remains schema-valid source
input, but it is insufficient evidence for Footprint v1 aggregation. If any
trade in an evaluated bucket has missing, unknown or unsupported aggression,
that bucket computation fails closed. The implementation must not infer a side,
drop the trade, split the volume or silently exclude it.

### Binding and provenance

A finalized Footprint result binds:

- `FootprintDefinitionV1` identity;
- concrete venue/instrument through canonical source dataset identity;
- requested interval and required bucket support;
- canonical source representation and record schema identity;
- source ordering policy;
- source coverage/finality evidence;
- immutable source revision/content evidence, such as source result identity or
  natural partition plus manifest/content hashes;
- implementation identity as reproducibility evidence.

Physical storage location, catalog UUID and process/runtime locator are
diagnostic only and do not redefine Footprint meaning. D06 introduces no
durable Representation artifact infrastructure.

## Runtime materialization

The accepted runtime foundation is implemented under
`quant_platform.representation` as immutable typed values and pure functions
for:

- `FootprintDefinitionV1`;
- deterministic definition serialization and identity;
- exact tick-grid validation;
- `FootprintLevel`, `FootprintBucket` and finalized historical results;
- caller-supplied source evidence and coverage/finality validation;
- deterministic aggregation from canonical `TradeRecord` inputs;
- explicit refusal of off-grid prices, invalid volumes, unusable aggression,
  duplicate supplied levels, insufficient support, non-final support and
  conflicting source provenance.

The implementation intentionally introduces no FeatureDefinition,
FeatureArtifact, feature provider, DataGateway adapter, storage materializer,
catalog mutation, live/PARTIAL topology or generic representation framework.

## Consequences

- D06 is frozen and complete for the bounded historical runtime foundation.
- E06 can depend on a canonical sparse integer-grid Footprint representation
  without changing E05 quantitative semantics.
- E04 remains responsible for FeatureArtifact/materialization identity.
- E06 remains responsible for canonical H01 orchestration and binding.
- Durable Footprint artifact schemas/materialization remain outside D06 unless
  a later accepted artifact/materialization authority selects them.

## Acceptance evidence

Executable proof is in `tests/test_historical_footprints_v1.py`.

The proof covers deterministic definition identity, runtime binding exclusion,
UTC epoch-aligned support, exact on-grid/off-grid decimal vectors, same-level
aggregation, buy-only and sell-only levels, unknown aggression fail-closed
behavior, sparse absent levels, covered zero-trade buckets, insufficient
support/finality refusal, conflicting provenance refusal, duplicate supplied
level refusal and compatibility with the E05 integer-level seam.

Existing regression evidence remains in `tests/test_feature_imbalance_v1.py`,
`tests/test_candle_definition_v1.py` and `tests/test_package_boundaries_v1.py`.
