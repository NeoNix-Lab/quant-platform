# Open Decisions

## Producer–Consumer Conformity Gate

Two sequential, non-circular gates are defined by
[ADR-0023](../decisions/ADR-0023-producer-consumer-conformity-gate-v1.md):
the **Contract Freeze Gate** (documentation-level; unlocks implementing the
conformity slices) and the **Conformity Implementation Gate** (runtime-level;
unlocks broader producer/consumer vertical expansion and Candle runtime).

The Contract Freeze Gate has **PASSED**. The Conformity Implementation Gate
remains **OPEN / IN PROGRESS**. The first real Human vertical / Golden Bybit
BTCUSDT E2E has now **PASSED**; this closes the runtime-level first-vertical
Golden milestone but does not close the complete gate.

The remaining gate path is:

```text
Human / Golden Bybit BTCUSDT E2E — PASS
        ↓
adversarial acceptance
        ↓
Candle ordering compatibility acceptance
        ↓
final Conformity Implementation Gate review
```

The successful Golden run exercised the real chain through canonical
materialization, durable manifests/coverage, S13 certification, S14 eligibility,
catalog publication and `DataGateway.scan()`, ending in an exact Golden match.
The acceptance artifacts and catalog rows are retained evidence rather than
cleanup targets.

## Source-acquired canonical dataset lineage

This is no longer an open first-vertical design decision.

[ADR-0025](../decisions/ADR-0025-source-acquired-canonical-dataset-lineage-v2.md)
resolves the composition gap discovered during Human E2E preparation:

- `dataset-manifest-v1` remains frozen and is never reinterpreted;
- `dataset-manifest-v2` introduces explicit dataset topology origin;
- canonical `source_acquired` datasets require a transform and no
  `derived_from` dataset parents;
- source provenance remains owned by `coverage-manifest-v1`;
- S14 accepts and verifies exactly zero `catalog.dataset_lineage` rows for that
  topology;
- no fake raw dataset, source registry or catalog migration is introduced.

Generalization of this model beyond the first implemented vertical remains
subject to normal future scope and review.

## Catalog schema-registry bootstrap

The first-vertical deployment prerequisite for `trade-v1` registration is also
resolved at implementation level on the Human E2E closeout branch. The
repository schema file remains semantic authority; bootstrap may create an
absent identical registration, accept an already-identical registration, and
must refuse conflicts rather than update or upsert an incompatible schema.

This is provisioning/runtime representation of an already-frozen schema, not a
new schema authority.

## Package Boundary / Modular Monolith Foundation v1

The following direction is resolved for the post-Conformity program order:

- one authoritative repository remains acceptable;
- one Python source root, `src/`, remains acceptable;
- the default target is a modular monolith;
- a mandatory Package Boundary / Modular Monolith Foundation v1 checkpoint
  occurs immediately after Conformity Implementation Gate PASS and before
  broad independent Producer/Consumer expansion;
- bounded ownership and dependency direction must become enforceable through
  package boundaries and architecture tests.

The following remain intentionally open for that checkpoint:

- exact bounded-context and package names;
- exact package hierarchy and dependency graph;
- executable host placement, including any future `apps/` layout;
- independently installable API, worker or client packages;
- service/deployment split and process topology;
- migration mechanics and sequence for current `quant_platform.data` modules.

ADR-0024 records this boundary without freezing the future package tree.

[PRODUCER_CONSUMER_CONFORMITY.md](../contracts/PRODUCER_CONSUMER_CONFORMITY.md)
freezes the bounded-read property, `RecordTimeBounds`, the ordering contract,
the DataGateway/CandleDefinition ordering-identity mapping, the Bybit
first-vertical eligibility profile, the physical `trade-v1` Parquet contract,
the `CanonicalContentHashV1` algorithm, the physical-vs-semantic-vs-result
identity matrix, the first-vertical certification rule, semantic catalog-rebuild
equality, and the manifest+coverage-to-catalog mapping.

The following remain explicitly open as future generalizations or implementation
choices:

- transport for a bounded historical read;
- concrete logical row/columnar return representation;
- whether `DatasetSnapshot` becomes a first-class public contract and, if so,
  its durable identity semantics;
- generalization of current S13/S14 publication mechanics beyond the first
  Bybit `trade-v1` vertical;
- deterministic ordering integration for a second venue;
- the general quality-report-to-lifecycle-state formula beyond the first
  vertical;
- a file-durable certification evidence artifact;
- live stream interface, cursor and identity semantics;
- schema compatibility/evolution policy beyond the explicitly accepted
  dataset-manifest-v2 decision.

## Must resolve before relevant implementation

### DataGateway and representations

- precise candle materialization identity;
- Candle ordering compatibility acceptance required by the current Conformity
  Gate.

DataGateway logical boundary and first implementation slice are resolved by
ADR-0019 and `docs/contracts/DATA_GATEWAY.md`.

### Market Data Ingest

Declared coverage versus observed event bounds is resolved by ADR-0022 and
`docs/contracts/DECLARED_COVERAGE.md`. The following remain open for broader
implementation:

- a catalog coverage relation plus DataGateway support for non-contiguous
  partition coverage;
- physical placement of a dataset's `_coverage/` directory across storage roots;
- crash-safe sealing, manifest publication, catalog reconciliation and retry
  details beyond the frozen bridge invariants;
- lifecycle-state mapping beyond the first Bybit `trade-v1` vertical;
- live/backfill overlap, source precedence, repair triggering and duplicate
  resolution;
- deduplication when a source provides no native identity;
- deterministic ordering integration for a second venue;
- future live consumer stream semantics;
- versioned L1, L2 and L3/MBO contracts.

See [Market Data Ingest](MARKET_DATA_INGEST.md),
[Market Data Ingest Contracts](../contracts/MARKET_DATA_INGEST_CONTRACTS.md) and
[Declared Coverage](../contracts/DECLARED_COVERAGE.md).

### Storage Lifecycle

Storage roots, placement, tiering, data protection, backups, capacity, health
and storage-pressure behavior remain operational Data Plane concerns. Open work
includes:

- hot/cold/deep-cold migration thresholds and relocation protocol;
- retention and deletion authority;
- backup destination, topology, frequency and restore validation;
- capacity thresholds, time-to-full estimation and pressure actions;
- checkpoint-state protection and monitoring technology;
- general preserved-legacy-source certification/immutability policy beyond the
  completed Human E2E evidence.

See [Storage Lifecycle](STORAGE_LIFECYCLE.md).

### Execution

- Order/Fill lifecycle;
- stop, target, bracket and OCO semantics;
- same-bar/intrabar conflict resolution;
- partial-fill model;
- multi-asset scope;
- session semantics.

### RL

- StrategicState, StrategicAction and StrategicReward;
- ExecutionState, ExecutionAction and ExecutionReward;
- structural isolation of the two tasks.

### Experiment persistence

- canonical Study/Trial/Run/Artifact storage schema.

## Safe to defer

- exact L3 contract until a real L3 feed is selected;
- calibration-method choice;
- full custom/reward-code sandbox until user-authored code is supported;
- complete paper/live operational mechanics until their roadmap phases.

FeatureDefinition versus FeatureSetDefinition and candle runtime/materialization
are resolved by ADR-0016 and ADR-0017.
