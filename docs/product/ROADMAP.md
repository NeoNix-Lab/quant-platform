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
open. Until the implementation gate passes, Producer and Consumer may advance
concurrently only on work that directly advances the shared conformity seam
and its evidence.

[ADR-0023](../decisions/ADR-0023-producer-consumer-conformity-gate-v1.md)
freezes two sequential, non-circular gates between the accepted foundation
contracts (DataGateway v1, CandleDefinition v1, Declared Coverage v1) and
broader vertical expansion:

```text
foundation contracts
        ↓
CONTRACT FREEZE GATE — PASSED
  (documentation-level: ADR-0023 and
   PRODUCER_CONSUMER_CONFORMITY.md accepted)
        ↓
  unlocks: implementing the conformity slices — NOT broader
  vertical expansion
        ↓
CONFORMITY IMPLEMENTATION GATE — IN PROGRESS / PARTIAL
  (runtime-level: the conformity slices are implemented and pass the
   required tests, including the golden Bybit BTCUSDT vertical)
        ↓
  unlocks: producer AND consumer vertical expansion, AND Candle runtime
      ↙       ↘
producer      consumer
vertical      vertical
development   development
```

The completed slices are Shared Semantic Primitives v1, Canonical Parquet
Materializer v1, Manifest + Coverage Emission v1 and Bounded DataGateway finite
read v1. Golden Conformity Acceptance Support v1 is also READY as reusable gate
support, but it is not the Golden E2E proof. The remaining critical path is
Publication Certification, the manifest/coverage/certification-to-catalog
bridge, the Golden Bybit E2E, adversarial acceptance, Candle ordering
compatibility acceptance and the gate review. See `SCOPE.md` and
`CAPABILITY_MAP.md` for the current ledger.

This is a temporary integration gate pair, not a permanent architecture layer.
Passing both gates does not add a phase to the dependency order below; it
reopens the parallelism that already existed.

## Phase A — controlled convergence before Conformity Implementation Gate PASS

The following are blocked until the implementation gate passes:

- full Candle runtime;
- Feature runtime and broader Representation expansion;
- broader producer verticals;
- live ingest and multi-venue runtime;
- L1/L2/L3/MBO runtime;
- unrelated consumer or producer vertical expansion.

The conformity slices themselves may proceed in their frozen dependency order.
The next authorized Producer slice is **Publication Certification runtime v1**.

## Phase B — full bidirectional expansion after Conformity Implementation Gate PASS

After the Conformity Implementation Gate passes, the temporary cross-track
synchronization constraint is removed. Producer and Consumer development may
resume independently and concurrently, subject to normal ownership, dependency
direction, frozen contracts, architecture gates and explicit slice scopes.
The post-gate model is not architecture-unconstrained.

Before that broad expansion begins, the mandatory cross-cutting structural
checkpoint is **Package Boundary / Modular Monolith Foundation v1**. It does
not add a product phase or renumber phases 0–15; it establishes bounded package
ownership and architecture tests inside the existing single-repository,
single-`src/` modular-monolith default.

Immediately after that checkpoint, run **Legacy Capability Harvest Audit v1**
before broad bidirectional expansion. The legacy repository is evidence only;
each target capability is classified ADOPT / ADAPT / REVIEW / REJECT against
current canonical semantics, temporal correctness and dependency baggage.

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

The consumer dependency track remains canonical. In parallel, the Data Plane
producer track is:

```text
Market Data Ingest architecture and contracts
        ↓
Declared coverage contract
        ↓
Contract Freeze Gate — PASSED
        ↓
Historical Trades publication conformity:
  Canonical Parquet Materializer — COMPLETE
  Manifest + Coverage Emission — COMPLETE
  Bybit eligibility profile — PARTIAL until authoritative certification
        ↓
Publication Certification — NEXT
        ↓
Manifest/Coverage/Certification → Catalog Publication Bridge
        ↓
Golden Bybit E2E + adversarial acceptance + Candle ordering proof
        ↓
Conformity Implementation Gate — IN PROGRESS / PARTIAL
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

The producer and consumer tracks meet through the existing published Data
Plane contracts and the DataGateway boundary. Storage safety is cross-cutting.
Persistent high-volume L2/L3/MBO collection is not operationally READY without
capacity monitoring, pressure behavior, source-data protection and recovery.

Provenance and identity primitives are required as soon as their entities are
implemented; they do not wait for Phase 8. Research may continue across
phases. Each phase starts only through an explicit scope.

Repository Synchronization & Integrity Foundation v1 is the infrastructure
gate between the reviewed DataGateway Contract v1 and DataGateway runtime
implementation. It does not add a product capability or renumber the phases.

Phase 2 (Representation foundation) has its semantic contract frozen and
accepted (ADR-0021, CandleDefinition v1). Candle runtime implementation is
explicitly postponed until the Conformity Implementation Gate passes, because
the bounded historical DataGateway read, Canonical Parquet Materializer and
Manifest + Coverage Emission are implemented, but authoritative Publication
Certification and the Publication Bridge still require completion and proof.
Candle runtime remains blocked until the complete seam is proven and the
Conformity Implementation Gate passes.
