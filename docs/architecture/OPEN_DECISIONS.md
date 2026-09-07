# Open Decisions

This document records only decisions that are still live or intentionally deferred. Accepted ADRs/contracts remain semantic authority; [`../product/CAPABILITY_DAG.md`](../product/CAPABILITY_DAG.md) records which atoms each decision blocks and when a gate is activated.

## Resolved foundation — reference only

The following are no longer open and must not be re-litigated without contradictory current authority:

```text
Contract Freeze Gate                                  PASSED
Conformity Implementation Gate                        PASSED
Human / Golden Bybit BTCUSDT E2E                     PASS
Package Boundary / Modular Monolith Foundation v1   COMPLETE
Legacy Capability Harvest Audit v1                   COMPLETE
ASS-01 Application ownership/enforcement             COMPLETE
```

Resolved architecture includes:

- canonical Data Access owner `quant_platform.access`;
- shared canonical primitives/Producer under `quant_platform.data`;
- deliberate shared physical seam `quant_platform.data.parquet`;
- source-specific ownership under `quant_platform.source_adapters`;
- application composition owner/package `application` / `quant_platform.application`;
- current runtime owner DAG and executable-orchestration enforcement;
- API-first direction: clients/API transport -> application services -> domain/DataGateway;
- Consumer API semantic selector/result/error boundary;
- `trade-v1`, Declared Coverage, CandleDefinition v1, first-vertical conformity/publication semantics and source-acquired lineage v2.

Legacy repositories remain evidence/reference only and are never runtime dependencies.

## Decision-gate policy

An `OPEN_BLOCKING` decision is not automatically a current project. It becomes active only when a selected dependent atom reaches its gate. An `OPEN_DEFERABLE` decision remains deliberately unresolved until the stated real-world evidence trigger exists.

No decision is frozen merely to improve a roadmap-completeness percentage.

## DG-A — Representation / Feature integration

Blocks: `D05,D06,E02,E04,E06`.

Must resolve before canonical Feature runtime / H01 integration:

- precise Candle materialization identity: how persisted Candle results bind RepresentationDefinition/CandleDefinition, source dataset/partition evidence, temporal support and implementation identity;
- canonical Footprint representation/grain for order-flow features, including validated tick grid, price-level ordering/adjacency and temporal availability;
- canonical `FeatureDefinition` identity/fields/versioning and availability semantics;
- canonical `FeatureArtifact` identity/provenance/materialization semantics;
- H01 integration rule binding the pure imbalance kernel to canonical Footprint input and FeatureDefinition/FeatureArtifact provenance.

Does **not** block:

- ASS-02 in-process historical application service;
- on-demand historical Candle computation (`D03`);
- pure H01 kernel (`E05`).

Do not create a generic provider/plugin framework as part of this gate.

## DG-B — Historical / Live data convergence

Blocks: `A10,A11,A16,B04,B06`.

Must resolve before live/backfill convergence:

- general quality-report -> lifecycle mapping beyond the accepted first Bybit `trade-v1` vertical;
- explicit representation and DataGateway semantics for non-contiguous covered intervals;
- live/backfill overlap and source precedence;
- repair triggering and idempotent revision/retry behavior;
- duplicate resolution, including sources without native identity;
- live DataGateway stream/cursor identity and deterministic resume/replay semantics.

Historical first-vertical publication/read remains authoritative and does not need re-proof.

## DG-C — Market-data depth (L1/L2)

Blocks: `A13,A14`.

Trigger: a concrete source/feed is selected for L1 or L2 implementation.

Resolve from observed source evidence:

- versioned L1 schema/identity/ordering/coverage/provenance;
- versioned L2 snapshot/increment semantics;
- reconstruction ordering, gap detection/recovery and duplicate rules.

Do not require a second venue merely to implement a first L1/L2 source.

Exact L3/MBO semantics remain separately deferable until a real L3 feed exists.

## DG-D — Application configuration / ASS-03

Blocks: `C05`, therefore full ASS-03 convergence.

Current facts:

- runtime/domain owners accept resolved dependencies/values rather than discovering configuration;
- existing executable/test tooling has divergent configuration conventions;
- ASS-01 records finite exact-edge orchestration debt.

Before ASS-03, choose one explicit application configuration-resolution convention and entry-point boundary sufficient to migrate the existing tools behind real application services.

Do not introduce a general dependency-injection, plugin or configuration framework merely for cleanup.

## DG-E — Validation semantics

Blocks: `F06,F07,F08`.

Resolve before claiming canonical validation / labeling / robust comparison:

- temporal availability rule used by Feature/Research/Validation;
- purge/embargo/warmup semantics at fold boundaries;
- Label/Outcome horizon and censoring semantics;
- lockbox/hidden-evaluation boundary;
- exact DSR/PBO estimator definitions, input return series, trial population, comparable-fold semantics and pinned numeric vectors.

H14 DSR/PBO remains isolated: it does not block unrelated H01/Feature work before this gate is activated.

## DG-F — Strategy / Execution semantics

Blocks: `G04,H03` and therefore the complete deterministic replay vertical.

Resolve with pinned/adversarial vectors:

- session calendars, DST and session-boundary behavior;
- cooldown/eligibility semantics;
- stop/target/bracket/OCO behavior;
- same-bar/intrabar conflicts;
- partial fills and conflict ordering.

Keep Strategy upstream of Execution. Do not let execution simulation redefine strategy semantics.

## DG-G — Experiment / RL / Jobs

Blocks: `I02,I06,I07,J03`.

These decisions are independent sub-gates and should be activated separately:

### Experiment persistence (`I02`)

Resolve one canonical Study/Trial/Run/Artifact persistence model. Avoid competing persistence stores/models. Restart/query/resume must preserve identity and idempotency.

### Strategic RL (`I06`)

Freeze StrategicState/StrategicAction/StrategicReward only when the strategic-RL runtime is selected. It must not absorb execution-control variables/objectives.

### Execution RL (`I07`)

Freeze ExecutionState/ExecutionAction/ExecutionReward only when execution RL is selected. It must remain structurally separate from the strategic task.

### Job runtime (`J03`)

Before durable long-running operations, freeze submission identity, status/lifecycle, retry/idempotency, result identity and failure semantics. Do not infer transport/process topology from the Job contract.

## DG-H — Operational safety

Blocks operationally sensitive atoms `K02,K03,K05,K06,K07,K08,K09,K10` and dependent live/product work.

This is progressive, not monolithic.

### Runtime identity (`K02`)

Before production runtime identities are created/changed, resolve service identities, database roles, filesystem ACLs, credential disposition and least-privilege boundaries. Human/bootstrap E2E privilege is not production authorization.

### Observability (`K03`)

Before paper/live claims, define the minimum externally observable health/provenance/failure transitions required by the selected runtime. Exact technology/SLOs remain implementation-local until needed.

### Pressure/protection/relocation (`K05-K07`)

Resolve capacity thresholds/time-to-full actions, source-data protection authority and crash-safe relocation protocol before activating those control actions. No silent deletion.

### Backup/restore/deletion (`K08-K09`)

Restore proof must precede deletion authority. Resolve backup destination/topology/frequency only against the selected recovery objective. No protected or sole recoverable evidence may be deleted.

### Checkpoint/recovery (`K10`)

Requires live acquisition plus observability and restore evidence. Freeze cursor/checkpoint crash/restart semantics before implementation.

## Explicitly deferable decisions

The following are classified and therefore not roadmap unknowns:

### Second provider / multi-capability resolution (`A12`,`C06`)

Wait for a real second venue or second representation. The future mechanism may be explicit composition, capability lookup, registry or another design; do not freeze it from first-provider symmetry.

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
- concrete bounded-batch Python class/method naming where semantics are already frozen;
- Parquet writer library/compression/row-group/page/dictionary settings;
- client presentation details;
- observability implementation technology after required signals are known;
- local reversible code organization within established ownership/dependency boundaries.

## Future package / process topology

ADR-0024 intentionally does not freeze:

- package names beyond currently exercised/implemented owners;
- future `apps/`/host layout;
- independently installable API/worker/client packages;
- microservice/service split;
- deployment/process topology.

Resolve these only when a concrete runtime/deployment proposition requires them. The default remains the current modular monolith.

## Activation rule

Before a decision gate is opened:

1. identify the selected dependent atom in `CAPABILITY_DAG.md`;
2. verify its other dependencies are satisfied;
3. scope only the exact unresolved decision proposition(s);
4. credit accepted authority/evidence;
5. resolve the minimum semantics needed for that atom;
6. leave unrelated gates/deferable decisions untouched.
