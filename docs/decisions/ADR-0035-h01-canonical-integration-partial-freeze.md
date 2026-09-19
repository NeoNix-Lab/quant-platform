# ADR-0035 - H01 canonical integration v1 partial semantic freeze

**Status:** ACCEPTED

**Date:** 2026-09-19

## Context

E06 is the remaining canonical-H01 integration atom on the accepted first Feature vertical.
Its prerequisites are already established:

- D06 FootprintDefinition v1 is frozen and complete under ADR-0027;
- E02 FeatureDefinition v1 is frozen and complete under ADR-0026;
- E04 FeatureArtifact v1 is frozen and complete under ADR-0034;
- E05 provides the accepted pure diagonal/stacked imbalance kernel.

The current package-boundary authority also remains in force: `quant_platform.application`
may compose Representation and Feature capabilities, while Feature-owned runtime must not
acquire a direct dependency on Representation-owned runtime. E07 generic provider extension
remains deferable until real second-provider/capability evidence exists.

This ADR freezes only the E06 propositions already decided for the first H01/MVP vertical.
It does **not** declare E06 fully decision-complete or implementation-ready. The remaining
open propositions are listed explicitly below and keep E06 `OPEN_BLOCKING`.

## Decision

### 1. Cross-owner composition belongs to Application

The concrete first-vertical composition is:

```text
Application composition
        ↓
D06 FINAL Footprint result
        ↓
H01 Feature evaluation
        ↓
E05 kernel
```

`quant_platform.application` owns the cross-owner composition. H01 quantitative and
Feature semantics remain Feature-owned. E06 must not create a `feature -> representation`
dependency and must not introduce a new shared abstraction merely to bridge this single
first-vertical case.

### 2. Diagonal and stacked imbalance are distinct FeatureDefinitions

The first H01 vertical exposes two distinct semantic observables:

- Diagonal Imbalance;
- Stacked Imbalance.

They therefore have distinct E02 `FeatureDefinition` identities and produce distinct
`FeatureObservation` identities. `imbalance_ratio` is semantic/identity-bearing for the
diagonal observable. `stacked_min_levels` is semantic/identity-bearing only for the stacked
observable; the stacked definition also carries the imbalance threshold semantics it uses.

The exact governed `feature_key` values and exact OutputContract payloads are intentionally
not frozen by this ADR.

### 3. Observation support and shape are bucket-scoped

For the first vertical, one finalized D06 Footprint bucket is the concrete evaluation support
coordinate. Each observable produces at most one E02 `FeatureObservation` per eligible FINAL
Footprint bucket.

Each observation value is a structured per-level result for the whole bucket, not one
FeatureObservation per price level. Price-level coordinates remain inside the structured
value. The E02 support/reference meaning is the current concrete Footprint observation/bucket;
no wall-clock window/lookback primitive is introduced by E06.

Conceptually:

```text
FINAL Footprint bucket T
        ├──> one Diagonal FeatureObservation for T
        └──> one Stacked FeatureObservation for T
```

### 4. Covered-empty FINAL Footprint buckets produce FINAL empty observations

D06 already distinguishes complete covered-empty buckets from missing/insufficient support.
E06 preserves that distinction.

If a FINAL Footprint bucket has complete authoritative support and zero level rows:

```text
Footprint levels = []
        ↓
Diagonal FeatureObservation = FINAL, value = []
Stacked FeatureObservation  = FINAL, value = []
```

Missing or insufficient source/support evidence remains a non-observation condition; it must
not be converted to an empty FINAL observation.

### 5. D06 exact-decimal values may be converted deterministically to E05 float inputs for H01 v1

The first H01 vertical accepts deterministic conversion from D06 exact-decimal price-level
volume values to the float calculation representation currently consumed by E05.

This does not make float values authoritative Footprint evidence and does not redefine D06
identity/provenance semantics. The exact D06 result/binding remains the upstream authority;
float is only the bounded E05 calculation representation. E06 must not reconstruct upstream
identity or provenance from converted float values.

A materially different numerical contract would require a new decision/version rather than
silently changing this v1 seam.

### 6. E04 provenance binds the exact immutable D06 result/binding evidence

For durable H01 materialization, E06 carries forward the exact immutable D06 result/binding
evidence as the authoritative upstream input evidence for E04 rather than independently
re-expanding and duplicating the underlying trade lineage.

Current D06 runtime evidence already carries the necessary transitive facts, including the
Footprint definition identity, exact bucket/support evidence, source dataset identity,
natural partition identities, manifest/content hashes, source request/result identities,
coverage/finalization evidence, implementation identity and result identity.

E06 must preserve that exact binding through FeatureObservation/materialization. It must not
replace it with a generic opaque hash or reconstruct a competing provenance model.

## Explicitly not frozen by this ADR

E06 remains `OPEN_BLOCKING` until the following propositions are decided:

1. **Stacked dependency shape** — whether the Stacked FeatureDefinition consumes the same D06
   Footprint input directly or consumes the Diagonal FeatureObservation through E02
   feature-on-feature composition.
2. **Exact governed FeatureDefinition contracts** — canonical `feature_key` values and exact
   OutputContract payloads for Diagonal and Stacked Imbalance.
3. **FeatureSet/materialization bundle** — whether the first durable H01 materialization is one
   FeatureSetDefinition containing both observables or separate FeatureSetDefinitions/artifacts.

The existing `E06 -> F02` planning edge is not reinterpreted by this ADR. Whether generic F02
requires E06 or only H01-backed EventSpecs require E06 remains a separate governance question.

## Boundaries

This partial freeze does not authorize or require:

- a generic Feature executor/DAG engine;
- provider/plugin infrastructure;
- E07 implementation;
- D05 Candle materialization;
- a new shared Representation/Feature bridge abstraction;
- cache/rehydration policy;
- new persistence topology;
- changes to E02, E04, D06 or E05 semantics beyond the explicit v1 seam above.

The first H01 vertical is deliberately concrete. Future features reuse the generic E02/E04
semantic foundations and add their own concrete kernels/bindings; common extension machinery
is extracted only when a real second case demonstrates the need.

## Consequences

- E06 decision space is narrowed but not closed.
- The first-vertical owner direction is fixed: Application composes D06 and Feature; Feature
  retains H01 meaning.
- Diagonal and Stacked Imbalance are distinct canonical observables with one structured
  observation per FINAL Footprint bucket.
- Covered-empty buckets remain explicit successful FINAL observations with empty values.
- E05 may continue using its current float calculation representation for H01 v1 without
  weakening D06 provenance authority.
- E04 materialization must bind the exact immutable D06 result/binding evidence rather than a
  second lineage model.
- E06 remains `MISSING` and `OPEN_BLOCKING` until the three explicitly open propositions above
  are frozen and bounded runtime implementation is separately authorized.
