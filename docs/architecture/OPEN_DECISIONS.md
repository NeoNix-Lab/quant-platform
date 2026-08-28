# Open Decisions

## Must resolve before relevant implementation

### DataGateway and representations

- precise candle materialization identity;
- required dataset-manifest evolution path.

DataGateway logical boundary and first implementation slice are resolved by
ADR-0019 and `docs/contracts/DATA_GATEWAY.md`. The following remain open for
the relevant implementation work:

- whether `DatasetSnapshot` should become a first-class public contract; if so,
  whether it should use an immutable partition/content reference or an
  independently materialized snapshot artifact. Catalog-query replay alone is
  explicitly insufficient for durable reproducibility;
- the concrete logical row/columnar batch return representation;
- the live stream interface and identity/cursor semantics;
- schema-v2 compatibility and evolution policy.

### Market Data Ingest

The producer-side capability is inside the Data Plane; it does not replace the
DataGateway consumer boundary. The following remain open for implementation:

- the exact population rule for declared partition coverage versus observed
  first/last event bounds;
- crash-safe sealing, manifest publication, catalog reconciliation, and
  idempotent retry details;
- the mapping from quality reports to `valid`, `degraded`, and `invalid`;
- live/backfill overlap, source precedence, repair triggering, and duplicate
  resolution;
- deduplication when the source provides no native identity;
- deterministic ordering integration for a second venue;
- future live consumer stream, cursor, and identity semantics;
- versioned L1, L2, and L3/MBO contracts.

See [Market Data Ingest](MARKET_DATA_INGEST.md) and
[Market Data Ingest Contracts](../contracts/MARKET_DATA_INGEST_CONTRACTS.md).

### Storage Lifecycle

Storage roots, placement, tiering, data protection, backups, capacity, health,
and storage-pressure behavior are operational Data Plane concerns. Their
implementation remains open:

- hot/cold/deep-cold migration thresholds and relocation protocol;
- retention and deletion authority;
- backup destination, topology, frequency, and restore validation;
- capacity thresholds, time-to-full estimation, and pressure actions;
- checkpoint-state protection and monitoring technology.

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

FeatureDefinition versus FeatureSetDefinition and candle runtime/materialization are resolved by ADR-0016 and ADR-0017.
