# Open Decisions

## Must resolve before relevant implementation

### DataGateway and representations

- precise candle materialization identity;
- required dataset-manifest evolution path.

DataGateway logical boundary and first implementation slice are resolved by
ADR-0019 and `docs/contracts/DATA_GATEWAY.md`. The following remain open for
the relevant implementation work:

- whether to expose `DatasetSnapshot` using immutable partition-set reference,
  catalog query replay, or a copied/materialized result;
- the concrete logical row/columnar batch return representation;
- the live stream interface and identity/cursor semantics;
- schema-v2 compatibility and evolution policy.

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
