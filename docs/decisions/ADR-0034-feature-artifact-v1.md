# ADR-0034 - FeatureArtifact v1

**Status:** ACCEPTED

**Date:** 2026-09-18

## Context

E02 (ADR-0026) already separated `FeatureDefinition` semantic identity from
materialization. E04 needed the remaining runtime foundation: one durable
reusable feature-set result with deterministic semantic identity, exact
source/support binding, reproducible implementation evidence, content
integrity, FINAL-only durability and immutable correction semantics --
without collapsing `FeatureDefinition`, `FeatureSetDefinition`,
`FeatureObservation`, `DatasetIdentity` or physical storage identity into one
concept, and without `quant_platform.features` gaining any
`DataGateway`/execution/catalog access (package-boundary rule: `feature` may
depend only on `feature`/`shared` owners).

Credited compatibility evidence:

- ADR-0016 already separates `FeatureDefinition`, `FeatureSetDefinition` and
  materialized artifact identity.
- ADR-0026 already provides deterministic `FeatureDefinitionId`, support
  semantics, finality and `FeatureObservationIdentity`.
- catalog `feature_set_definitions` already exposes the natural semantic key
  `(slug, version)` independently from the surrogate UUID.
- `DatasetIdentity`/manifest contracts already carry feature-set natural
  identity, dataset/partition identity, source lineage, coverage and content
  evidence.
- B04 (ADR-0029) already establishes that accepted coverage authority
  permits non-contiguous eligible coverage.

## Decision

Adopt E04 FeatureArtifact v1 under `quant_platform.features.artifacts` as
the bounded runtime foundation: deterministic `FeatureArtifactIdentity`,
portable `FeatureSetDefinitionIdentity` using the existing `(slug, version)`
natural key (never the catalog surrogate UUID), immutable `BoundInputEvidence`,
`SupportShape` support evidence, explicit `implementation_code_identity`,
`FeatureArtifactContentIdentity`, FINAL-only materialization eligibility,
duplicate/conflict classification (`classify_registration`), and a pure
independent-recomputation equivalence helper
(`values_semantically_equivalent`/`recomputation_equivalent`) driven by
caller-supplied constituent `FeatureDefinition`/`OutputContract` semantics.

### Declared/consumed support is a `SupportShape`, not a bare interval

A single contiguous `CoverageInterval` cannot represent every valid exact
support shape once B04 non-contiguous coverage is accepted authority.
`SupportShape` is an explicit, order-independent, non-overlapping set of
intervals: adjacent (touching) intervals canonicalize into one merged
interval so `[a,c)` and `[a,b)+[b,c)` always produce the identical identity,
and empty (`start == end`) intervals are discarded before that
canonicalization so they can never masquerade as non-empty materialized
support. `declared_materialized_support`
(`FeatureArtifact`) and `declared_support` (`FeatureArtifactContentIdentity`)
both carry `SupportShape` end to end.

### FINAL-only sealing and the attributable-evidence principle

`seal_feature_artifact()`/`require_final_observations()` verify the supplied
`observations` are exactly the caller-declared `expected_observation_identities`
universe -- no fewer, no more, all FINAL -- and that
`causal_available_at` ("earliest legal consumption time", ADR-0026) is never
conflated with support-shape membership, a structurally different axis.
Proving that the declared universe is itself the *true* complete one E06
derived from the real evaluation grid is E06's caller responsibility: E04 has
no DataGateway/execution access to verify that independently, so an
incomplete universe defeats the gate as a known, tracked limitation rather
than a provable E04 invariant. The same reasoning applies to
`rehydrate_feature_artifact()`: its `expected_identity` check is a
consistency safeguard against accidental catalog-row mismatch, not a
security boundary, since nothing in `quant_platform.features` can
independently prove a payload came from a real prior seal. This is the same
attributable-evidence principle established for K06 (ADR-0032) and A10
(ADR-0033).

### Construction is sealed against both direct use and `dataclasses.replace()`

`FeatureArtifact` cannot be constructed directly: a private `_seal_token`
guard restricts construction to `seal_feature_artifact()` (new artifacts,
after the FINAL-only proof gate) and `rehydrate_feature_artifact()` (trusted
catalog reconstruction). The token is an `InitVar`, not a stored field:
a stored field would be copied automatically by `dataclasses.replace()`
(and the newer `copy.replace()`), letting
`replace(sealed, implementation_code_identity="unproven")` trivially produce
a new, unproven FINAL artifact identity from any already-sealed instance.
An `InitVar` cannot be retrieved from the original instance, so `replace()`
falls back to the default and the construction guard fails closed exactly as
for direct construction.

### Numerical equivalence is exact and parameter-strict

`values_semantically_equivalent` applies only the rule the governing
`OutputContract`'s `NumericalEquivalence` declares (`EXACT`, `QUANTIZED`,
`TOLERANT`), rejects any parameter it does not recognize for that kind
before applying kind-specific logic (an unrecognized parameter is itself
identity-bearing contract semantics, never silently dropped), and computes
`QUANTIZED` rounding and the `TOLERANT` threshold/comparison in exact
`fractions.Fraction` arithmetic rather than `Decimal` operators -- `Decimal`
arithmetic (unlike construction) applies the caller's ambient
`decimal.getcontext()` precision/rounding, so two identical comparisons
could otherwise disagree purely because unrelated code elsewhere in the
process changed that context first.

### Feature-set slug rule matches canonical manifest authority

`FeatureSetDefinitionIdentity.slug` validates against the same identifier
rule as canonical manifest authority's `quant_platform.data.manifests`
`_IDENTIFIER` (duplicated locally, not imported, since `manifests` is owned
by `producer`, outside the `feature` package-boundary allow-list) -- E04
consumes the existing `(slug, version)` natural key unchanged, and a
stricter local rule would reject authoritative feature-layer dataset
identities that manifest validation already accepted.

## Runtime materialization

Implemented under `quant_platform.features.artifacts`:
`FeatureArtifactIdentity`, `FeatureSetDefinitionIdentity`, `SupportShape`,
`BoundInputEvidence`, `BoundSourceDataset`, `BoundSourcePartition`,
`BoundOutputPartition`, `FeatureArtifactContentIdentity`,
`ConstituentFeatureOutput`, `FeatureArtifact`, `FeatureArtifactLifecycle`,
`require_final_observations`, `seal_feature_artifact`,
`rehydrate_feature_artifact`, `verify_matches_request`,
`classify_registration`, `ArtifactRegistrationOutcome`,
`values_semantically_equivalent`, `recomputation_equivalent`.

The implementation intentionally introduces no execution graph, provider
selection, byte persistence, E06 canonical H01 binding decision or
cache/storage policy.

## Consequences

- E04 is frozen and complete for the bounded v1 materialization runtime;
  F01, I04 and E06 canonical H01 integration are unblocked.
- E06 (and any other consumer of `require_final_observations`/
  `rehydrate_feature_artifact`) inherits the caller-discipline obligations
  documented above: it must derive and supply the true expected observation
  universe, and only a catalog-loading seam may call
  `rehydrate_feature_artifact()`.
- D05 candle materialization identity remains a separate, unresolved DG-A
  branch; it is not a prerequisite of E04 or canonical H01.

## Acceptance evidence

Executable proof is in `tests/test_feature_artifact_v1.py` (90 targeted
tests) plus `tests/test_package_boundaries_v1.py` (feature package may only
depend on `feature`/`shared` owners) and the directly relevant
`tests/test_feature_definition_v1.py`, `tests/test_dataset_manifest_v1.py`,
`tests/test_dataset_manifest_v2.py`, `tests/test_partition_manifest_v1.py`
and `tests/test_coverage_manifest_v1.py` suites.
