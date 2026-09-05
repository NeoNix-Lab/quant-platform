# Quant Platform Roadmap

The roadmap expresses dependency direction, not day estimates. Exact scope may evolve through ADRs and `SCOPE.md`; product completeness remains the target.

0. Repository and governance foundation
1. Data access and identity foundation: DataGateway, catalog-backed reads and provenance
2. Representation foundation: CandleDefinition identity, closed/partial semantics and equivalence tests
3. Feature foundation: FeatureDefinition, FeatureSetDefinition and FeatureArtifact identity, source dataset/partition provenance, implementation identity, providers and materialization
4. Research and outcome foundation: Hypothesis/Event/Outcome identity, reproducibility, event studies and sweeps
5. Validation and labeling: warmup, walk-forward, purge, embargo, lockbox and censoring
6. Strategy and decision foundation: StrategySpec, policies, DecisionIntent, risk and sizing
7. Execution, replay and portfolio: orders, fills, positions, accounting and deterministic replay
8. Unified Experiment Orchestration and Persistence: Study, Trial, Run, Artifact cross-referencing, comparison, reproduction and canonical experiment persistence
9. Canonical API and job runtime
10. Supervised ML
11. Strategic RL
12. Execution RL
13. Clients: CLI, TUI and App UI
14. Paper and shadow operation
15. Live operation

## Producer–Consumer Conformity Gate

The repository is in **Phase A — Controlled Bidirectional Convergence**. The
Contract Freeze Gate has passed; the Conformity Implementation Gate remains
open. Human/Golden Bybit BTCUSDT E2E has now passed, but broader Producer and
Consumer expansion remains blocked until the remaining gate acceptance and
final review complete.

[ADR-0023](../decisions/ADR-0023-producer-consumer-conformity-gate-v1.md)
freezes two sequential, non-circular gates between the accepted foundation
contracts and broader vertical expansion:

```text
foundation contracts
        ↓
CONTRACT FREEZE GATE — PASSED
        ↓
conformity implementation slices
        ↓
Human / Golden Bybit BTCUSDT E2E — PASSED
        ↓
adversarial acceptance
        ↓
Candle ordering compatibility acceptance
        ↓
final Conformity Implementation Gate review
        ↓
CONFORMITY IMPLEMENTATION GATE — PASS
        ↓
producer AND consumer vertical expansion + Candle runtime
```

The completed slices are Shared Semantic Primitives v1, Canonical Parquet
Materializer v1, Manifest + Coverage Emission v1, Bounded DataGateway finite
read v1, Publication Certification runtime v1 (S13 Phases 1–3), Publication
Eligibility Bridge v1 (S14 / S13.5 Phases 4–5), and Golden Conformity Acceptance
Support v1. The first real Human/Golden vertical is now also complete and has
produced an exact Golden match through `DataGateway.scan()`.

During first-vertical integration, ADR-0025 introduced `dataset-manifest-v2` so
direct source/archive → canonical publication can be represented without a fake
raw parent, while source provenance remains owned by coverage evidence. The
same branch also adds explicit catalog schema-registry bootstrap for the frozen
record schema. These are narrow first-vertical remediations, not authorization
for broader runtime expansion.

## Phase A — controlled convergence before Conformity Implementation Gate PASS

Blocked until the implementation gate passes:

- full Candle runtime;
- Feature runtime and broader Representation expansion;
- broader producer verticals;
- live ingest and multi-venue runtime;
- L1/L2/L3/MBO runtime;
- unrelated consumer or producer vertical expansion.

The Human/Golden Bybit BTCUSDT vertical is no longer the next milestone. The
next authorized work is **adversarial acceptance**, followed by **Candle
ordering compatibility acceptance** and the **final Conformity Implementation
Gate review**.

## Human Golden E2E milestone — COMPLETE

The operator acceptance run for Bybit BTCUSDT `2024-01-15` matched the frozen
Golden expectation exactly:

```text
rows              1,105,145
buy                 553,875
sell                551,270
other                      0
first              2024-01-15T00:00:00.492Z
last               2024-01-15T23:59:59.931Z
S13                pass
S14                valid
coverage gaps      0
DataGateway        OPEN → READING → COMPLETED
batches            17
max batch size     65,536
Golden             exact match
```

The successful source fingerprint, physical artifact SHA256 and canonical
content hash are retained in the Human E2E closeout evidence. This milestone is
runtime-level gate evidence; it is not by itself the final Conformity Gate PASS.

## Phase B — full bidirectional expansion after Conformity Implementation Gate PASS

After the Conformity Implementation Gate passes, the temporary cross-track
synchronization constraint is removed. Producer and Consumer development may
resume independently and concurrently, subject to normal ownership, dependency
direction, frozen contracts, architecture gates and explicit slice scopes.

Before broad expansion begins, the mandatory cross-cutting structural
checkpoint remains **Package Boundary / Modular Monolith Foundation v1**,
followed immediately by **Legacy Capability Harvest Audit v1**.

```text
Conformity Implementation Gate PASS
            ↓
Package Boundary / Modular Monolith Foundation v1
            ↓
Legacy Capability Harvest Audit v1
            ↓
Broad independent Producer / Consumer expansion
```

## Parallel producer track

```text
Market Data Ingest architecture and contracts
        ↓
Declared coverage contract
        ↓
Contract Freeze Gate — PASSED
        ↓
Canonical Parquet Materializer — COMPLETE
        ↓
Manifest + Coverage Emission — COMPLETE
        ↓
Publication Certification S13 — COMPLETE
        ↓
Publication Eligibility Bridge S14 — COMPLETE
        ↓
Source-acquired canonical lineage v2 — COMPLETE on closeout branch
        ↓
Catalog schema-registry bootstrap — COMPLETE on closeout branch
        ↓
Human vertical / Golden Bybit BTCUSDT E2E — PASS
        ↓
Adversarial acceptance — NEXT
        ↓
Candle ordering compatibility acceptance
        ↓
Final Conformity Implementation Gate review
        ↓
Package Boundary / Modular Monolith Foundation v1
        ↓
Legacy Capability Harvest Audit v1
        ↓
Capacity monitoring foundation
        ↓
Storage health / pressure behavior
        ↓
RAW/source protection
        ↓
Safe storage placement and relocation
        ↓
Backup / restore foundation
        ↓
Backup verification
        ↓
Retention/deletion authority
        ↓
Backfill / repair
        ↓
Live Trades pilot
        ↓
Multi-venue Trades
        ↓
L1 → L2 → L3/MBO
        ↓
Advanced recovery and reconciliation
```

The producer and consumer tracks meet through the existing published Data Plane
contracts and the DataGateway boundary. Storage safety is cross-cutting.
Persistent high-volume L2/L3/MBO collection is not operationally READY without
capacity monitoring, pressure behavior, source-data protection and recovery.

Provenance and identity primitives are required as soon as their entities are
implemented; they do not wait for Phase 8. Research may continue across phases.
Each phase starts only through an explicit scope.

Repository Synchronization & Integrity Foundation v1 remains the infrastructure
gate between the reviewed DataGateway Contract v1 and DataGateway runtime
implementation. It does not add a product capability or renumber the phases.

Phase 2 (Representation foundation) has its semantic contract frozen and
accepted (ADR-0021, CandleDefinition v1). Candle runtime remains explicitly
blocked until adversarial acceptance, Candle ordering compatibility acceptance
and final Conformity Implementation Gate review pass.
