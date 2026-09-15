# Open Decisions

This document records only decisions that are still live or intentionally deferred. Accepted ADRs/contracts remain semantic authority; [`../product/CAPABILITY_DAG.md`](../product/CAPABILITY_DAG.md) records which atoms each decision blocks and when a branch of a gate family is activated.

## Resolved foundation — reference only

The following are no longer open and must not be re-litigated without contradictory current authority:

```text
Contract Freeze Gate                                  PASSED
Conformity Implementation Gate                        PASSED
Human / Golden Bybit BTCUSDT E2E                     PASS
Package Boundary / Modular Monolith Foundation v1   COMPLETE
Legacy Capability Harvest Audit v1                   COMPLETE
ASS-01 Application ownership/enforcement             COMPLETE
ASS-02 in-process Application service                COMPLETE
DG-D Application configuration semantics             RESOLVED
DG-A FeatureDefinition v1 semantics                  FROZEN / COMPLETE
DG-A FootprintDefinition v1 semantics                FROZEN / COMPLETE
DG-H Capacity observation                            RESOLVED / COMPLETE
DG-H PressurePolicyDefinition v1                     FROZEN / COMPLETE
```

The completed two-stage Producer–Consumer Conformity Gate remains governed by [ADR-0023](../decisions/ADR-0023-producer-consumer-conformity-gate-v1.md). Its gate states above are historical accepted foundation, not live decisions.

Resolved architecture includes:

- canonical Data Access owner `quant_platform.access`;
- shared canonical primitives/Producer under `quant_platform.data`;
- deliberate shared physical seam `quant_platform.data.parquet`;
- source-specific ownership under `quant_platform.source_adapters`;
- application composition owner/package `application` / `quant_platform.application`;
- current runtime owner DAG and executable-orchestration enforcement;
- API-first direction: clients/API transport -> application services -> domain/DataGateway;
- Consumer API semantic selector/result/error boundary;
- completed ASS-02 in-process path: C02 semantic selector resolution + C03 result/error translation;
- `trade-v1`, Declared Coverage, CandleDefinition v1, FeatureDefinition v1, FootprintDefinition v1, PressurePolicyDefinition v1, first-vertical conformity/publication semantics and source-acquired lineage v2.

Legacy repositories remain evidence/reference only and are never runtime dependencies.

### Resolved DG-D / C05 configuration semantics

The Application configuration decision is frozen as:

```text
CLI / environment
      ↓
executable boundary: acquire + resolve input only
      ↓
typed immutable capability-specific Application config
      ↓
quant_platform.application: concrete composition owner
      ↓
Catalog / DataGateway / source / producer capabilities
```

Rules:

- executable tooling owns CLI/environment acquisition and parsing;
- precedence is **explicit CLI > environment > declared default > explicit failure**;
- Application receives resolved values only and does not know their acquisition source;
- Application-facing config is typed, immutable and capability-specific;
- `quant_platform.application` owns concrete composition and does not read process arguments/environment directly;
- no generic DI container, service locator, provider registry or plugin/config framework is introduced.

This resolves the C05 decision state only. C05 implementation remains missing, and C04/ASS-03 remains blocked until that implementation exists.

## Decision-gate policy

An `OPEN_BLOCKING` decision is not automatically a current project. It becomes active only when a selected dependent atom reaches that proposition on its transitive dependency path.

A gate-family name groups related decisions for navigation; it is **not** a requirement to resolve every sibling proposition in the family.

An `OPEN_DEFERABLE` decision remains deliberately unresolved until its stated real-world evidence trigger exists. No decision is frozen merely to improve a completeness percentage.

## DG-A — Representation / Feature integration

This family has independent branches.

### Candle materialization (`D05`)

Activate only when persisted Candle results are selected. Resolve how a persisted Candle binds CandleDefinition/Representation identity, source dataset/partition evidence, temporal support and implementation identity.

`D05` is not a prerequisite of canonical H01 integration.

### FeatureArtifact (`E04`)

Activate only after the FeatureDefinition path is selected and artifact/materialization behavior is needed. Resolve artifact identity/provenance and equivalence of cached vs recomputed output.

### Footprint + canonical H01 (`D06`,`E06`)

For canonical H01, the actual path is now:

```text
D06 footprint representation (FROZEN / COMPLETE by ADR-0027)
E02 FeatureDefinition (FROZEN / COMPLETE by ADR-0026)
E04 FeatureArtifact
E05 pure H01 kernel (already RESOLVED)
E06 canonical H01 integration
```

D06 has frozen canonical price-level grain, validated tick grid,
ordering/adjacency, temporal availability and provenance binding. The remaining
live decisions on this branch are E04 FeatureArtifact and E06 canonical H01
integration.

Do not activate Candle materialization merely because it is in DG-A. Do not create a generic provider/plugin framework. FeatureDefinition v1 and FootprintDefinition v1 are no longer open; ADR-0026 and ADR-0027 are the accepted authorities for those foundations.

## DG-B — Historical / Live data convergence

This family also has separate repair and live branches.

### Repair branch (`A16`,`B04`,`A10` as required)

Activate only the propositions needed by the selected repair slice:

- general quality-report -> lifecycle mapping beyond the accepted first vertical;
- explicit non-contiguous coverage semantics where required;
- repair triggering, precedence and idempotent revision/retry behavior;
- duplicate resolution where required by repair semantics.

Historical repair does not activate live-cursor semantics by default.

### Live branch (`A11`,`B06` + only required shared decisions)

Before live acquisition/cursor implementation, resolve:

- historical/live overlap and source precedence;
- duplicate rules for the selected source;
- deterministic live cursor identity and resume/replay semantics;
- only those repair/shared quality/coverage propositions that are on the selected live path;
- the relevant DG-H operational prerequisites on that path.

Implemented checkpoint/recovery (`K10`) follows the live acquisition capability it checkpoints; it is not an implementation prerequisite of `A11`.

## DG-C — Market-data depth

### L1 branch (`A13`)

Activate when a concrete L1 feed is selected. Resolve versioned L1 schema, identity, ordering, coverage and provenance from observed source evidence.

Selecting L1 does **not** activate L2.

### L2 branch (`A14`)

Activate only when L2 is selected, after the declared L1 dependency. Resolve snapshot/increment semantics, deterministic reconstruction ordering, gap handling and duplicate rules.

Exact L3/MBO semantics remain separately deferable until a real L3 feed exists.

## DG-E — Validation semantics

### Validation / labeling branch (`F06`,`F07`)

Activate for the Validation -> Strategy/ML path. Resolve:

- temporal availability rule used by Feature/Research/Validation;
- purge/embargo/warmup semantics at fold boundaries;
- Label/Outcome horizon and censoring semantics;
- lockbox/hidden-evaluation boundary.

### DSR/PBO branch (`F08`)

Activate only when robust-comparison/DSR-PBO capability is selected. Resolve exact estimator definitions, input return series, trial population, comparable-fold semantics and pinned numeric vectors.

`F08` does **not** block Strategy or supervised-input paths whose dependency chain runs through `F07` rather than `F08`.

## DG-F — Strategy / Execution semantics

Blocks `G04,H03` when their Strategy/Replay path is selected.

Resolve with pinned/adversarial vectors:

- session calendars, DST and session-boundary behavior;
- cooldown/eligibility semantics;
- stop/target/bracket/OCO behavior;
- same-bar/intrabar conflicts;
- partial fills and conflict ordering.

Keep Strategy upstream of Execution. Do not let execution simulation redefine strategy semantics.

## DG-G — Experiment / RL / Jobs

Independent branches:

### Experiment persistence (`I02`)

Resolve one canonical Study/Trial/Run/Artifact persistence model. Restart/query/resume must preserve identity and idempotency; avoid competing persistence stores/models.

### Strategic RL (`I06`)

Freeze StrategicState/StrategicAction/StrategicReward only when the strategic-RL runtime is selected. It must not absorb execution-control variables/objectives.

### Execution RL (`I07`)

Freeze ExecutionState/ExecutionAction/ExecutionReward only when execution RL is selected. It must remain structurally separate from the strategic task.

### Job runtime (`J03`)

Before durable long-running operations, freeze submission identity, status/lifecycle, retry/idempotency, result identity and failure semantics. Do not infer transport/process topology from the Job contract.

## DG-H — Operational safety

This family is progressive and non-monolithic.

### Runtime identity (`K02`)

Before production runtime identities are created/changed, resolve service identities, database roles, filesystem ACLs, credential disposition and least-privilege boundaries. Human/bootstrap E2E privilege is not production authorization.

### Observability (`K03`)

Before paper/live claims or checkpoint/recovery runtime, define the minimum externally observable health/provenance/failure transitions required by the selected runtime. Exact technology/SLOs remain local until needed.

### Source protection (`K06`)

Resolve protection authority and reconstruction guarantees. `K06` depends on the selected source and pressure policy; it does not depend on backup/restore.

### Tier relocation (`K07`)

If relocation is selected, freeze crash-safe old-or-new-valid placement semantics. This is a sibling downstream use of source protection, not a prerequisite of backup/restore.

### Backup/restore (`K08`)

After source protection, resolve recovery objective, destination/topology/frequency as required, and prove independent restoration of required identities. Tier relocation need not be implemented first.

### Retention/deletion (`K09`)

Restore proof must precede deletion authority. No protected or sole recoverable evidence may be deleted.

### Checkpoint/recovery (`K10`)

`K10` implementation requires live acquisition `A11`, observability `K03` and restore evidence `K08`. Freeze cursor/checkpoint crash/restart semantics before implementing K10. It follows, rather than cyclically precedes, A11 implementation.

## Explicitly deferable decisions

The following are classified and therefore not roadmap unknowns:

### Second provider / multi-capability resolution (`A12`,`C06`)

Wait for a real second venue or second representation. Do not freeze a generic resolver from first-provider symmetry.

### L3/MBO (`A15`)

Wait for a real L3/MBO feed and map its semantics without invention.

### Durable DatasetSnapshot (`B05`)

Wait for a concrete durable replay/reproduction requirement. Catalog-query replay alone remains insufficient; reference-vs-artifact shape is not frozen now.

### Schema evolution beyond accepted versions (`B08`)

Wait for a real next schema/version requirement.

### Feature provider extension (`E07`)

Wait for a real second provider/capability need; no generic plugin framework now.

### Multi-asset execution (`H06`)

Wait for explicit multi-asset product scope.

### Canonical API transport (`J02`)

Consumer API semantics are already frozen. Concrete HTTP/gRPC/Arrow Flight/WebSocket/other transport, serialization, pagination/streaming and runtime host remain deferred until a real remote/client need exists.

## Implementation-local choices — not governance blockers

Unless a future accepted contract says otherwise, these remain local choices:

- internal helper/class names;
- concrete bounded-batch Python class/method naming where semantics are frozen;
- Parquet writer library/compression/row-group/page/dictionary settings;
- client presentation details;
- observability implementation technology after required signals are known;
- local reversible code organization within established ownership/dependency boundaries.
