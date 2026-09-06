# Open Decisions

## Producer–Consumer Conformity Gate

Two sequential, non-circular gates are defined by
[ADR-0023](../decisions/ADR-0023-producer-consumer-conformity-gate-v1.md):
the **Contract Freeze Gate** (documentation-level; unlocks implementing the
conformity slices) and the **Conformity Implementation Gate** (runtime-level;
unlocks broader producer/consumer vertical expansion and Candle runtime). The
Contract Freeze Gate has **PASSED** and the Conformity Implementation Gate has
**PASSED**. Human Golden E2E, Adversarial Acceptance A1–A9 and Candle Ordering
Compatibility are PASS; the Final Conformity Gate Review is APPROVE with
`BLOCKERS: NONE` and `IMPORTANT: NONE`. The temporary integration gate pair is
concluded.

```text
Human Golden E2E PASS
    → Adversarial Acceptance A1–A9 PASS
    → Candle Ordering Compatibility PASS
    → Final Conformity Gate Review APPROVE
    → Conformity Implementation Gate PASSED
    → Package Boundary / Modular Monolith Foundation v1 COMPLETE
    → Legacy Capability Harvest Audit v1 COMPLETE
```

Legacy Capability Harvest Audit v1 is **COMPLETE**. Its accepted classification
is recorded in `docs/legacy/ADOPTION_LEDGER.md`; H01 remains the first selected
legacy harvest candidate and H14 DSR/PBO remains the sole non-blocking REVIEW
residue.

`DataSliceMetadata.canonical_content_hash` remains an additive future extension
and was non-blocking for the Gate. Semantic relocation invariance passed; the
operational relocation runtime remains missing and independent of Gate closure.

## Source-acquired canonical dataset lineage

This is no longer an open first-vertical decision. Accepted
[ADR-0025](../decisions/ADR-0025-source-acquired-canonical-dataset-lineage-v2.md)
and its implementation establish the required evolution path:

- `dataset-manifest-v1` remains frozen and is never reinterpreted;
- `dataset-manifest-v2` represents canonical `source_acquired` datasets with
  `derived_from` absent, a required transform and exactly zero lineage edges;
- the historical SQLite remains a source/archive, not a raw `DatasetIdentity`,
  fake parent or proxy dataset;
- source provenance remains owned by CoverageManifest evidence.

## Server Access & Runtime Identity Hardening v1

The Human E2E used operator/bootstrap privileges sufficient to exercise
publication end to end. Those privileges are acceptance/bootstrap evidence
only. They do not define the production runtime authorization model.

The separate operational follow-up must restore or audit canonical server
access, service identities, filesystem ACLs, database roles and credential
disposition. Human administrators continue to use SSH; local services must use
canonical service identities; future normal remote consumers remain behind the
Canonical API, application services and DataGateway.

This follow-up was non-blocking and remains independent of the completed
Conformity Implementation Gate. It does not authorize an interim HTTP API,
Canonical API runtime implementation, new identities, ACL changes or database
role changes in this closeout.

## Package Boundary / Modular Monolith Foundation v1

The following direction is resolved for the post-Conformity program order:

- one authoritative repository remains acceptable;
- one Python source root, `src/`, remains acceptable;
- the default target is a modular monolith;
- bounded ownership and dependency direction must be enforceable through
  package boundaries and architecture tests.

Package Boundary / Modular Monolith Foundation v1 is **COMPLETE**. It was
executed from baseline `9c938a5`; final reviewed candidate
`900c128ddf063b9e4ea393596fb37aa3c0692ba8` merged through PR #25 as
`7d531fcd8eb46b3d562de93ccae9c2352f2706fa` after independent review and
exact-head CI passed. Its disposition is recorded in ADR-0024 under
"Implementation outcome". Legacy Capability Harvest Audit v1 is now also
**COMPLETE**.

Resolved by Package Boundary and subsequent ASS-01 work, and therefore no
longer open:

- the Data Access bounded context is `quant_platform.access`; shared canonical
  primitives and Producer remain under `quant_platform.data`;
  `quant_platform.data.parquet` remains a deliberate shared physical seam; and
  `quant_platform.source_adapters` remains source-specific;
- the package hierarchy and dependency graph for the current runtime modules,
  mechanically enforced by `tests/test_package_boundaries_v1.py`;
- migration mechanics and sequence for current `quant_platform.data` modules;
- the application-service composition owner is `application`, with canonical
  package `quant_platform.application`;
- executable orchestration under `tools/` is now governed as a dependency
  source and may not introduce new domain/runtime bypasses around the application
  seam.

The following remain intentionally open:

- bounded-context and package names beyond the implemented Data Access and
  Application seams;
- executable package hierarchy for future bounded contexts;
- executable host placement, including any future `apps/` layout;
- independently installable API, worker or client packages;
- service/deployment split and process topology.

ADR-0024 records this boundary without freezing the future package tree.

## Application Service Seam v1

The accepted API-first architecture already places application services between
future clients/API transport and domain/DataGateway capabilities. ASS-01 closes
the previously unowned in-process composition boundary without introducing a
network/runtime platform.

ASS-01 — Application Service Ownership & Architecture Enforcement is
**COMPLETE** through PR #27, merged as
`066e7cd577104fb2c8f657430402b79cd58ba9aa` after independent exact-head review
and CI success.

Resolved by ASS-01:

```text
owner   = application
package = quant_platform.application
```

Current ownership permits `application` to compose the existing `access`,
`producer`, `source`, `physical` and `shared` owners while no existing runtime
owner may depend on `application`. The owner graph remains acyclic.

Verification composition is intentionally asymmetric:

```text
runtime !-> tools/tests
tools   !-> tests
tests   -> tools/runtime/test-support is permitted
```

Existing historical tool bypasses are not silently accepted: they are recorded
as finite exact-edge, self-cleaning ASS-03 debt. New executable orchestration
must go through the application seam. Dynamic import/code-execution bypasses
are fail-closed consistently with the runtime dependency analyzer.

ASS-01 does not implement an application use case. The following remain separate
future work:

- ASS-02: first canonical application-service vertical, including semantic
  request resolution and the already-frozen application result/error semantics;
- ASS-03: convergence of existing executable orchestration and configuration
  resolution behind the application seam;
- Canonical API transport/runtime, Job runtime and product clients at their
  later roadmap milestones.

The following decision is newly explicit and intentionally **OPEN**:

- **multi-venue / multi-representation capability resolution** inside the
  application composition layer. The first vertical may compose currently
  available source-specific semantics directly; the general mechanism
  (registry, capability lookup, explicit composition or another design) must
  not be frozen before a real second venue/representation provides evidence.

No next implementation slice is selected by this closeout.

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

### Application services

ASS-02 may use the already-frozen Consumer API semantic selector/result/error
rules without choosing transport. Before broader multi-venue or
multi-representation application composition is generalized, resolve the exact
capability-resolution mechanism using observed second-provider evidence.

ASS-03 must converge the finite pre-ASS-01 tool-domain/tool-test debt and the
currently divergent configuration-resolution conventions without introducing a
general dependency-injection or plugin framework merely for cleanup.

### DataGateway and representations

- precise candle materialization identity;

DataGateway logical boundary and first implementation slice are resolved by
ADR-0019 and `docs/contracts/DATA_GATEWAY.md`. The following remain open for
the relevant implementation work:

- whether `DatasetSnapshot` should become a first-class public contract; if so,
  whether it should use an immutable partition/content reference or an
  independently materialized snapshot artifact. Catalog-query replay alone is
  explicitly insufficient for durable reproducibility;
- the concrete logical row/columnar batch return representation;
- the live stream interface and identity/cursor semantics;
- schema compatibility and evolution policy beyond the accepted
  `dataset-manifest-v2` decision.

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
- complete paper/live operational mechanics until their roadmap phases;
- transport, remote host and product-client implementation while application
  work remains in-process and does not require them.

FeatureDefinition versus FeatureSetDefinition and candle runtime/materialization are resolved by ADR-0016 and ADR-0017.
