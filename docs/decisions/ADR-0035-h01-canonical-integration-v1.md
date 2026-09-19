# ADR-0035 — H01 canonical integration v1

**Status:** ACCEPTED

**Date:** 2026-09-19

## Context

E06 is the first concrete Feature vertical. Its foundations are already accepted and implemented:

- D06 FootprintDefinition v1 is frozen and complete under ADR-0027;
- E02 FeatureDefinition v1 is frozen and complete under ADR-0026;
- E04 FeatureArtifact v1 is frozen and complete under ADR-0034;
- E05 provides the accepted pure diagonal/stacked imbalance kernel.

ADR-0024 also remains authoritative: `quant_platform.application` may compose Representation and Feature capabilities, while Feature-owned runtime must not acquire a direct dependency on Representation-owned runtime.

E06 must close the concrete D06 -> Feature -> E05 -> E02/E04 H01 path without inventing a generic Feature executor, provider/plugin framework, or second provenance model.

## Decision

### 1. Application owns cross-owner composition

The first-vertical composition is:

```text
quant_platform.application
        ↓
D06 FINAL Footprint result
        ↓
H01 Feature evaluation
        ↓
E05 kernel
```

Application owns cross-owner composition only. H01 quantitative and Feature semantics remain Feature-owned.

E06 must not introduce a `feature -> representation` dependency and must not introduce a new shared bridge abstraction merely for this first concrete vertical.

### 2. Diagonal Imbalance and Stacked Imbalance are independent FeatureDefinitions

H01 exposes two distinct canonical observables:

```text
order_flow.diagonal_imbalance
order_flow.stacked_imbalance
```

Both use semantic version `1`.

They are distinct E02 `FeatureDefinition` identities and produce distinct `FeatureObservation` identities.

Both consume the canonical D06 Footprint directly. Stacked Imbalance does **not** consume the Diagonal FeatureObservation. E05 may internally reuse its diagonal calculation while evaluating stacked imbalance, but that is kernel implementation detail and does not create feature-on-feature semantics.

Conceptually:

```text
                 D06 FINAL Footprint
                /                   \
               ↓                     ↓
Diagonal Imbalance Feature     Stacked Imbalance Feature
```

### 3. Canonical input contract and support

Both FeatureDefinitions use one atomic E02 input contract:

```text
contract_key         = footprint.price_level
contract_version     = 1
required_observables = level_index, buy_volume, sell_volume
shape                = atomic
```

Both declare:

```text
support        = current()
input_maturity = FINAL_ONLY
initialization = none / minimum_history = 0
```

One eligible FINAL D06 Footprint bucket is one concrete support coordinate.

D06 Footprint-definition parameters such as bucket duration and tick size remain Representation parameters and participate in D06 identity/provenance. They are not duplicated as Feature semantic parameters.

### 4. Semantic parameters

#### Diagonal Imbalance

The only Feature-owned semantic parameter is:

```text
imbalance_ratio
```

It is an E02 exact-decimal semantic parameter, default `"3"`, identity-bearing, and must be strictly greater than `1`.

#### Stacked Imbalance

The Feature-owned semantic parameters are:

```text
imbalance_ratio
stacked_min_levels
```

`imbalance_ratio` is an exact-decimal semantic parameter, default `"3"`, identity-bearing, and must be strictly greater than `1`.

`stacked_min_levels` is an integer semantic parameter, default `3`, identity-bearing, and must be at least `2`.

The two FeatureDefinitions are independent. Their `imbalance_ratio` values may differ; equal values in a particular H01 FeatureSet are configuration coincidence, not semantic coupling.

The canonical H01 definition constructors must reject parameter values outside the E05 domain even where the generic E02 parameter-schema primitive cannot express an exclusive lower bound by itself.

### 5. One observation per FINAL Footprint bucket

For each observable, one eligible FINAL D06 Footprint bucket produces at most one E02 `FeatureObservation`.

The observation value is a structured ordered per-level record for the whole bucket, not one FeatureObservation per price level.

Price-level entries are ordered by increasing `level_index`.

### 6. Covered-empty buckets are successful FINAL empty observations

D06 already distinguishes complete covered-empty buckets from missing/insufficient support. E06 preserves that distinction.

For a FINAL Footprint bucket with authoritative complete support and zero level rows:

```text
Diagonal FeatureObservation = FINAL, value = []
Stacked FeatureObservation  = FINAL, value = []
```

Missing or insufficient support produces no FeatureObservation for that coordinate and must not be converted into an empty FINAL observation.

### 7. Canonical Diagonal output contract

The canonical OutputContract is:

```text
value_kind = RECORD
shape      = bucket_level_diagonal_imbalance_v1
dimension  = DIMENSIONLESS
```

Each per-level record contains exactly:

```text
level_index: int
ask_imbalance_ratio: number | null
bid_imbalance_ratio: number | null
imbalance_side: "ask" | "bid" | null
```

The canonical projection intentionally excludes E05 technical/redundant fields:

- `bar_id` — the E02 support identity already identifies the bucket;
- `buy_volume` / `sell_volume` — D06 owns those source observables;
- `total_volume` — derivable from D06 input;
- `delta` — a distinct quantitative observable and not part of Diagonal Imbalance.

E05 uses `NaN` to represent an absent adjacent counterparty. E06 must normalize that sentinel to `null` in the canonical RECORD value. This is required both for portable semantics and because E04 exact RECORD equality cannot treat `NaN` as recomputation-equal to itself.

Positive infinity produced by the accepted E05 zero-denominator ratio semantics remains the corresponding numerical result; a durable encoder used by E04 must preserve that value without silently coercing it.

### 8. Canonical Stacked output contract

The canonical OutputContract is:

```text
value_kind = RECORD
shape      = bucket_level_stacked_imbalance_v1
dimension  = DIMENSIONLESS
```

Each per-level record contains exactly:

```text
level_index: int
imbalance_side: "ask" | "bid" | null
stacked_imbalance: bool
stacked_run_length: int
```

The canonical projection excludes Footprint volumes, total volume, delta and diagonal ratios. Those values are either D06-owned inputs or Diagonal-Imbalance semantics and must not leak into the Stacked observable merely because E05 carries them internally.

The output contains one entry for every D06 Footprint level, including non-stacked levels. This preserves a deterministic one-to-one per-level projection and makes negative stacked results explicit.

### 9. Numerical boundary: D06 exact decimal -> E05 float

H01 v1 accepts deterministic conversion from D06 exact-decimal level volume values to the float calculation representation consumed by E05.

This conversion does not make float values authoritative Footprint evidence and does not redefine D06 identity/provenance semantics. The exact D06 result/binding remains the upstream authority; float is only the bounded E05 calculation representation.

E06 must not reconstruct upstream identity or provenance from converted float values. A materially different numerical contract requires a new semantic version/decision rather than silently changing this v1 seam.

### 10. E06 owns the true expected observation universe

E04 can prove that supplied observations exactly equal a caller-declared expected observation universe, but E04 cannot independently prove that the caller declared the true universe.

E06 owns that missing proposition for H01.

For requested evaluation support, E06 derives the exact expected H01 observation identities from the eligible FINAL D06 Footprint bucket grid:

```text
eligible FINAL bucket identities
        ×
{DiagonalDefinitionId, StackedDefinitionId}
        ↓
exact expected FeatureObservationIdentity universe
```

Covered-empty eligible buckets remain members of that universe and yield FINAL empty observations. Missing/insufficient-support coordinates are not fabricated into the universe as successful observations.

### 11. Provenance binds exact D06 result/binding evidence

For durable H01 materialization, E06 carries forward the exact immutable D06 result/binding evidence as E04 upstream input evidence rather than independently re-expanding the underlying trade lineage.

The accepted D06 result/source evidence already carries the transitive facts required for attribution, including Footprint definition identity, exact support/coverage, source dataset identity, natural partition identities, manifest/content hashes, source request/result identities, finalization evidence, implementation identity and result identity.

E06 must preserve that binding through FeatureObservation/materialization. It must not replace it with an opaque un-attributable hash or reconstruct a competing provenance model.

### 12. One canonical H01 FeatureSetDefinition

The first durable H01 bundle is one FeatureSetDefinition:

```text
slug    = h01_imbalance
version = 1

constituents:
- order_flow.diagonal_imbalance@1
- order_flow.stacked_imbalance@1
```

The constituent FeatureDefinition identities remain independent and include their own semantic parameters. The single FeatureSet is a materialization/bundle decision; it does not merge the two observables into one FeatureDefinition.

E04 materializes this bundle when durability is requested. Evaluation and durability remain distinct operations; E06 must not require every in-memory evaluation to create an artifact.

## Implementation readiness

The E06 semantic/architecture propositions required for the first H01 vertical are now frozen.

E06 therefore moves to:

```text
Decision: FROZEN
Implementation: MISSING
Execution status: implementation-ready for a separately bounded scope/issue
```

Implementation remains unauthorized until `SCOPE.md` or an equivalent bounded work item explicitly activates E06.

## Boundaries

This ADR does not authorize or require:

- a generic Feature executor/DAG engine;
- provider/plugin infrastructure or E07;
- D05 Candle materialization;
- a new shared Representation/Feature bridge abstraction;
- generic multi-feature orchestration beyond this concrete H01 vertical;
- cache/rehydration infrastructure absent a concrete need;
- new persistence topology;
- changes to D06, E02, E04 or E05 semantics beyond the explicit integration seam above.

Future features reuse the generic E02/E04 semantic foundations and add their own concrete kernels/bindings. Common extension machinery is extracted only when a real second case demonstrates the need.

## Consequences

- Application is the concrete cross-owner composition boundary.
- Diagonal and Stacked Imbalance are independent Footprint-derived observables.
- D06 duration/tick-size stay Representation parameters; Feature parameters are only the H01 thresholds described above.
- One FINAL Footprint bucket maps to one structured observation per constituent feature.
- Covered-empty buckets yield explicit FINAL empty observations.
- Canonical outputs exclude redundant D06/E05 technical fields.
- `NaN` is normalized to `null` before entering canonical Diagonal RECORD values.
- The exact D06 evidence chain remains provenance authority through E04.
- One `h01_imbalance@1` FeatureSet materializes both constituent observables.
- E06 now owns the exact H01 observation-universe derivation required by ADR-0034.
- E06 is semantically frozen and ready for bounded implementation; runtime remains `MISSING` until that work is completed and verified.
