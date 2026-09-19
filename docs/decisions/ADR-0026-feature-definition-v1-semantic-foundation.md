# ADR-0026 - FeatureDefinition v1 semantic foundation

**Status:** ACCEPTED

**Date:** 2026-09-15

## Context

ADR-0003 requires all derived market observables to share FeatureDefinition,
grain, availability, identity and lineage semantics. ADR-0006 makes temporal
availability a domain contract. ADR-0016 separates one semantic observable
(`FeatureDefinition`) from a versioned bundle (`FeatureSetDefinition`) and a
materialized artifact (`FeatureArtifact`).

E02 resolves the minimum FeatureDefinition v1 semantics needed before the
Feature/Research path, FeatureArtifact materialization, canonical H01
integration and validation availability policy can depend on feature identity.
The runtime foundation must make the decision executable, but it must not
absorb FeatureArtifact persistence, Footprint semantics, feature orchestration,
provider infrastructure, validation policy or storage topology.

Credited compatibility evidence:

- ADR-0003 requires shared feature semantics, but does not require concrete
  representation grain to be FeatureDefinition identity.
- ADR-0006 forbids decisions from consuming unavailable information.
- ADR-0016 keeps FeatureDefinition, FeatureSetDefinition and materialized
  artifact identity distinct.
- `src/quant_platform/features/imbalance.py` is a pure E05 quantitative kernel
  and does not own FeatureDefinition identity or FeatureArtifact creation.
- `CandleDefinitionV1` demonstrates deterministic semantic-definition
  serialization and the distinction between causal availability floor and
  nullable observed availability evidence.
- `experiments/identities.py::_freeze_json` demonstrates repository
  fingerprint conventions, but Feature parameter equivalence is governed by
  this ADR, not by Experiment semantics.

## Decision

Adopt `FeatureDefinition v1` as the canonical semantic foundation for one
derived observable.

### Definition identity

A `FeatureDefinition` identifies exactly one governed canonical `feature_key`
and one explicit governed `semantic_version`. External aliases may exist only
at input/configuration boundaries and must normalize to the canonical key before
construction. Aliases are not competing canonical identities.

`FeatureDefinitionId` is deterministic and content-derived from the complete
canonical semantic definition. Equivalent definitions must produce the same
identity across processes, runs and machines.

Identity-bearing semantics include:

- canonical `feature_key` and `semantic_version`;
- declared typed semantic parameter schema and canonical normalized parameter
  values;
- exactly one versioned `InputContract`, including required observables and
  explicit composite dependencies when present;
- declarative support/reference semantics;
- input maturity, availability and finality semantics;
- initialization, seed or minimum-history rules that change output;
- semantic `OutputContract`, including semantic dimension and numerical
  equivalence rules when applicable.

Identity excludes registry insertion order, implementation build/SHA/backend,
cache strategy, materialization strategy, concrete dataset, venue, instrument,
time range, physical locator, execution provenance and concrete representation
grain unless that grain/duration is itself an intrinsic semantic parameter.

### Parameters

Every feature family must declare the semantic schema for its identity-bearing
parameters: canonical names, semantic types/domains, defaults,
required/optional/null semantics and collection ordering semantics.

Canonicalization must resolve declared defaults, validate domains and semantic
types, normalize accepted external spellings to one canonical semantic value,
remove irrelevant mapping/serialization order, preserve ordered sequences,
sort explicitly unordered sets deterministically, and define whether omitted
and explicit null are equivalent or distinct.

`1` versus `1.0` is never left to generic JSON behavior. A parameter schema
must either normalize both to one declared semantic type/value or reject the
non-canonical form. Omitted default and explicit same default produce the same
identity.

Only semantic parameters participate in identity. Runtime, execution, storage
and materialization parameters are excluded.

### Input contracts

A `FeatureDefinition` references exactly one versioned `InputContract`
representing a semantic capability, not a concrete representation definition,
dataset, venue, instrument or grain. The contract may be atomic or a versioned
composite. Composite dependencies must be explicit and identity-bearing for the
composite contract.

A concrete representation or feature output is compatible only when it
explicitly satisfies the required contract and required observables. Producer
selection belongs to binding/evaluation context, not consumer
FeatureDefinition identity.

### Support

V1 support/reference semantics are a closed logical-observation model:

```text
current()
point(offset)
window(end_offset, length)
```

Offsets and lengths address ordinal logical observations of the required
InputContract. They do not imply elapsed wall-clock time, concrete grain,
continuity, sessions, gap duration, time decay or range selection over an
irregular stream.

E02 v1 deliberately does not define a wall-clock support-selection primitive.
A feature that requires selecting all inputs inside a trailing/leading
wall-clock interval over an irregular stream is unsupported until a separately
accepted additive primitive exists. Such semantics must not be hidden in
runtime code.

### Availability, finality and lifecycle

Temporal availability is intrinsic FeatureDefinition semantics.

Input maturity v1 has exactly two values:

```text
AVAILABLE
FINAL_ONLY
```

`AVAILABLE` permits consuming provisional required observations.
`FINAL_ONLY` requires final required observations before consumption.

Feature observations may follow:

```text
PROVISIONAL -> FINAL
```

While provisional, additional valid input may update the value for the same
observation identity. Finalization does not create another definition or
observation identity. `FINAL` is monotonic for the authoritative observation.

Every `FeatureObservation` must carry the semantic causal availability floor:
the earliest semantic time at which the value may legally be used. Observed
availability and observed finalization timestamps are separate evidence, may be
unknown/null for historical data, and must never be fabricated from event time,
bucket end, ingestion time or storage time.

For E02 v1 feature-on-feature consumption, if any contributing required input
observation is `PROVISIONAL` under an `AVAILABLE` requirement, the dependent
output remains `PROVISIONAL`. It can become `FINAL` only after all contributing
required observations are `FINAL` and the definition finality condition is
satisfied. More aggressive early finalization requires a later explicit
decision and proof.

Fold purge, embargo, lockbox and validation warm-up remain F06/F07 policy and
are not encoded in FeatureDefinition.

### FeatureObservation and non-observation

`FeatureObservation` is the runtime semantic representation of one
FeatureDefinition evaluated over one concrete semantic support. Its identity is
based on:

- `FeatureDefinitionId`;
- a bound `SupportIdentity` supplied by the input contract or equivalent
  binding context, sufficient to identify the actual observed support
  unambiguously.

Value and lifecycle state do not participate in observation identity.

If declared support, minimum history or initialization requirements are not
satisfied, no `FeatureObservation` exists. Insufficient or undefined support is
an explicit non-observation outcome, not a canonical provisional/null/NaN/zero
sentinel value. Downstream tabular/vector consumers may project absence into
nullable columns or masks, but that projection is not a canonical observation
value.

### Output contract

A `FeatureDefinition` declares a minimal semantic `OutputContract`: value kind,
shape and semantic dimension (`price`, `quantity`, `duration` or
`dimensionless`). Numeric output also declares the semantic numerical
equivalence contract needed to decide whether independently computed outputs
represent the same canonical value.

Exact, quantized, rounded, clipped, tick-normalized or otherwise semantic
numeric equivalence rules participate in FeatureDefinition identity when they
change observable meaning. Storage dtype, Arrow/Parquet encoding, backend byte
equality and generic test tolerance are non-semantic unless they alter
observable meaning.

## Runtime materialization

The accepted runtime foundation is implemented under
`quant_platform.features` as immutable typed values for:

- feature key governance and external alias normalization;
- `FeatureDefinition`, deterministic `FeatureDefinitionId` and canonical
  payload serialization;
- `InputContractV1`, atomic/composite shape and required observables;
- `SupportReference.current`, `SupportReference.point` and
  `SupportReference.window`;
- typed semantic parameter specs and canonical parameters;
- input maturity, availability, finality and initialization semantics;
- `OutputContract` and numerical equivalence semantics;
- `SupportIdentity`, `FeatureObservation`, `FeatureObservationIdentity`,
  lifecycle validation and explicit non-observation outcomes.

The implementation intentionally introduces no registry, provider/plugin
framework, graph engine, feature algorithm, storage/catalog persistence or
FeatureArtifact identity/materialization.

## Consequences

- E02 is frozen and complete for the bounded runtime foundation.
- FeatureDefinition-dependent slices can reference stable feature semantic
  identity without deciding materialization or orchestration.
- E04 remains responsible for FeatureArtifact/materialization identity and
  durable persistence.
- D06 remains responsible for Footprint grain/tick-grid/ordering/adjacency and
  availability.
- E06 remains responsible for canonical H01 integration and concrete acyclic
  binding/orchestration.
- F06/F07 remain responsible for validation purge, embargo, lockbox and
  fold-level policy.
- E07 remains deferable until a real second provider/capability need exists.

## Acceptance evidence

Executable proof is in `tests/test_feature_definition_v1.py`.

The proof covers deterministic identity, default equivalence, typed parameter
normalization/refusal, ordered versus unordered collections, omitted versus
explicit null semantics, map/schema order independence, distinct semantic
identity inputs, input contracts, support selector validation with no
wall-clock inference, lifecycle monotonicity, causal/observed temporal
evidence, non-observation outcomes and numerical-equivalence identity.

Existing E05 imbalance behavior remains governed by
`tests/test_feature_imbalance_v1.py`. Package ownership remains mechanically
enforced by `tests/test_package_boundaries_v1.py`.
