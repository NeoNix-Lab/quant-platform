# Scope: E02 FeatureDefinition v1 Semantic Foundation

## Objective

Materialize and implement `E02 - FeatureDefinition v1` so the repository has
both accepted canonical authority and a bounded runtime semantic/value model
under `quant_platform.features`.

This slice owns only the FeatureDefinition v1 foundation: semantic definition
identity, input contracts, support selectors, parameter canonicalization,
availability/finality, initialization/history semantics, output contracts,
runtime observation identity/evidence and explicit non-observation outcomes.

## Baseline

```text
base main = 32e750fad6276ecd81f6870c1b41d4738176abc8
branch    = agent/issue-51-8
atom      = E02
owner     = Feature Engine
```

At work start, local `origin/main` was verified as exactly
`32e750fad6276ecd81f6870c1b41d4738176abc8`.

## Accepted state after this slice

```text
E01  Definition/set/artifact separation      FROZEN / COMPLETE
E02  FeatureDefinition v1                    FROZEN / COMPLETE
E03  FeatureSet catalog/provider             RESOLVED / PARTIAL
E04  FeatureArtifact/materialization         OPEN_BLOCKING / MISSING
E05  H01 pure imbalance kernel               RESOLVED / MISSING in governance; pure kernel present
E06  H01 canonical integration               OPEN_BLOCKING / MISSING
```

`E02` is frozen by ADR-0026 and implemented as immutable typed values in
`src/quant_platform/features/definitions.py`.

## Included

- Create accepted ADR-0026 for FeatureDefinition v1.
- Update directly affected governance so E02 is no longer unresolved.
- Add immutable typed runtime values for governed feature keys, deterministic
  `FeatureDefinitionId`, versioned `InputContractV1`, closed support selectors,
  typed semantic parameters, maturity, availability/finality, initialization,
  output contracts, feature observations and explicit non-observation outcomes.
- Export the runtime surface through `quant_platform.features`.
- Add targeted executable tests for the new E02 propositions.
- Preserve existing E05 pure imbalance behavior.

## Excluded

- FeatureArtifact identity, materialization, caching or durable persistence.
- Footprint grain, tick-grid, ordering, adjacency or availability semantics.
- Generic feature-DAG construction, provider registry or execution engine.
- Validation purge, embargo, lockbox or fold warm-up policy.
- Storage/catalog topology, UI/API transport topology or runtime locators.
- Wall-clock support selectors.
- General schema framework or units algebra.
- Unrelated cleanup/refactor.

## Credited evidence

- Producer-Consumer Conformity `Contract Freeze Gate` and `Conformity
  Implementation Gate` remain PASSED under ADR-0023 and are not reopened.
- ADR-0003 establishes a unified Feature Engine semantic requirement.
- ADR-0006 establishes temporal availability as a domain contract.
- ADR-0016 keeps FeatureDefinition, FeatureSetDefinition and FeatureArtifact
  identity distinct.
- The E05 imbalance kernel remains pure quantitative evidence and does not own
  FeatureDefinition identity or FeatureArtifact creation.
- `CandleDefinitionV1` remains credited for deterministic semantic
  serialization and nullable observed availability evidence.
- Experiment identity helpers remain credited as fingerprint convention
  evidence only; E02 parameter equivalence is governed by ADR-0026.

## Acceptance

DONE means:

1. ADR-0026 is accepted and E02 is removed from unresolved governance.
2. `quant_platform.features` exposes the FeatureDefinition v1 runtime model.
3. Deterministic FeatureDefinition identity includes semantic parameters,
   input contract, support, maturity, availability/finality, initialization
   and output contract semantics.
4. Runtime/process state, concrete dataset, venue, instrument, grain, storage,
   materialization and execution provenance are excluded from definition
   identity.
5. Parameter defaults, typing, nullability and ordered/unordered collection
   semantics are executable and deterministic.
6. `current`, `point` and `window` are the only v1 support selectors and do not
   imply wall-clock duration.
7. Observation identity is definition plus bound support; value/lifecycle do
   not create a new identity.
8. `PROVISIONAL -> FINAL` is represented and `FINAL -> PROVISIONAL` is refused
   where the model owns transition validation.
9. Causal availability floor is required; observed availability/finalization
   evidence remains nullable and unfabricated.
10. Insufficient support is represented as non-observation, not a sentinel
    FeatureObservation value.
11. No FeatureArtifact persistence, provider framework, graph engine or
    cross-boundary ownership leak is introduced.

## Verification

Targeted checks for this slice:

```text
python tests/test_feature_definition_v1.py
python tests/test_feature_imbalance_v1.py
python tests/test_package_boundaries_v1.py
python -m compileall -q src tests
```

Repository-required authoritative checks should be run once the candidate is
stable.

## Downstream state

Unblocked by E02:

```text
F01  HypothesisSpec
E04  FeatureArtifact/materialization decision/implementation path
E06  canonical H01 path after D06/E04/E05
F06  validation availability path can reference FeatureDefinition semantics
```

Still blocked:

```text
E04  FeatureArtifact/materialization
D06  Footprint representation
E06  H01 canonical integration
F06/F07 validation leakage/label policy
E07  generic provider extension until real second-provider need exists
```
