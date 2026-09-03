# Open Decisions

## Producer–Consumer Conformity Gate

Two sequential, non-circular gates are defined by
[ADR-0023](../decisions/ADR-0023-producer-consumer-conformity-gate-v1.md):
the **Contract Freeze Gate** (documentation-level; unlocks implementing the
conformity slices) and the **Conformity Implementation Gate** (runtime-level;
unlocks broader producer/consumer vertical expansion and Candle runtime). The
Contract Freeze Gate has **PASSED**. The Conformity Implementation Gate remains
**OPEN / IN PROGRESS**: only the conformity slices and their gate evidence may
advance until it passes. The automated first-vertical publication path is now
implemented through S13 Publication Certification and S14 Publication
Eligibility/Verification; the next gate milestone is the Human vertical /
Golden Bybit BTCUSDT E2E. See ADR-0023 for the exact, separate exit criteria of
each gate.

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
first-vertical eligibility profile (non-null, unique `trade_id` — distinct
from generic `trade-v1` nullability, which is unchanged), the physical
`trade-v1` Parquet contract, the `CanonicalContentHashV1` algorithm, the
physical-vs-semantic-vs-result identity matrix, the first-vertical
certification rule (including the durable evidence model and
certification/publication sequencing), semantic catalog-rebuild equality, and
the manifest+coverage-to-catalog mapping. The following remain explicitly
open even after that freeze, as implementation choices deliberately left
tunable or future generalizations beyond the first implemented vertical:

- transport for a bounded historical read (HTTP streaming, gRPC, Arrow
  Flight, WebSocket, network pagination) — the contract freezes only the
  memory-bounded property, not a transport;
- the concrete Python type/method name for the bounded `scan()` capability and
  for `RecordTimeBounds`;
- Parquet writer implementation/library, compression, row-group size and
  page/dictionary settings;
- generalization of the current S13/S14 catalog transaction and publication
  mechanics beyond the first Bybit `trade-v1` vertical; the first-vertical
  implementation itself is no longer an open decision;
- second-venue ordering-identity integration (the mapping mechanism is frozen
  in `PRODUCER_CONSUMER_CONFORMITY.md` §8.2 OI2, including that a second venue
  needs its own eligibility profile analogous to §10; no second venue is added
  by this pass);
- the general quality-report-to-lifecycle-state formula beyond the first
  Bybit `trade-v1` vertical (the first-vertical minimum evidence rule is
  frozen in `PRODUCER_CONSUMER_CONFORMITY.md` §13; a general formula for
  other data kinds remains open per `MARKET_DATA_INGEST_CONTRACTS.md` §10);
- a file-durable `certification-evidence-v1` artifact (§13.3 CE5 explicitly
  does not design one; certification is instead treated as a reproducible
  operation, re-run on catalog loss).

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
DataGateway consumer boundary.

Declared coverage versus observed first/last event bounds is resolved by
ADR-0022 and `docs/contracts/DECLARED_COVERAGE.md`. The following remain open
for implementation:

- a catalog coverage relation plus the DataGateway change needed to read
  non-contiguous partition coverage. Until then such a partition cannot be
  published at all: `degraded` is not an option, because lifecycle state gates
  which partitions are read and never narrows their declared span;
- physical placement of a dataset's `_coverage/` directory when its partitions
  span hot, cold and deep-cold storage roots;
- crash-safe sealing, manifest publication, catalog reconciliation, and
  idempotent retry details beyond the bridge invariants (idempotency, natural
  identity keying, revision handling, storage-root mapping, hash provenance,
  fail-closed failure behavior) frozen in `PRODUCER_CONSUMER_CONFORMITY.md`
  §11.3;
- the mapping from quality reports to `valid`, `degraded`, and `invalid` for
  data kinds and vertical slices beyond the first Bybit `trade-v1` day (the
  first-vertical minimum evidence rule is frozen in
  `PRODUCER_CONSUMER_CONFORMITY.md` §10);
- live/backfill overlap, source precedence, repair triggering, and duplicate
  resolution;
- deduplication when the source provides no native identity;
- deterministic ordering integration for a second venue;
- future live consumer stream, cursor, and identity semantics;
- versioned L1, L2, and L3/MBO contracts.

See [Market Data Ingest](MARKET_DATA_INGEST.md),
[Market Data Ingest Contracts](../contracts/MARKET_DATA_INGEST_CONTRACTS.md) and
[Declared Coverage](../contracts/DECLARED_COVERAGE.md).

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
