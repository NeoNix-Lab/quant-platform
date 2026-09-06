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

**Phase A — Controlled Bidirectional Convergence is COMPLETE.** The Contract
Freeze Gate and Conformity Implementation Gate have passed. Human/Golden Bybit
BTCUSDT E2E, Adversarial Acceptance A1–A9 and Candle Ordering Compatibility are
PASS; the Final Conformity Gate Review is APPROVE with no blockers or important
findings. The temporary Producer/Consumer synchronization constraint is
removed.

[ADR-0023](../decisions/ADR-0023-producer-consumer-conformity-gate-v1.md)
freezes two sequential, non-circular gates between the accepted foundation
contracts (DataGateway v1, CandleDefinition v1, Declared Coverage v1) and
broader vertical expansion:

```text
foundation contracts
        ↓
CONTRACT FREEZE GATE — PASSED
        ↓
conformity implementation slices
        ↓
Human / Golden Bybit BTCUSDT E2E — PASS
        ↓
Adversarial Acceptance A1–A9 — PASS
        ↓
Candle Ordering Compatibility — PASS
        ↓
Final Conformity Gate Review — APPROVE
        ↓
CONFORMITY IMPLEMENTATION GATE — PASSED
        ↓
Package Boundary / Modular Monolith Foundation v1 — COMPLETE
        ↓
Legacy Capability Harvest Audit v1 — COMPLETE
        ↓
broader independent Producer / Consumer expansion — UNLOCKED
```

The completed slices are Shared Semantic Primitives v1, Canonical Parquet
Materializer v1, Manifest + Coverage Emission v1, Bounded DataGateway finite
read v1, Publication Certification runtime v1 (S13 Phases 1–3), and
Publication Eligibility Bridge v1 (S14 / S13.5 Phases 4–5). Golden Conformity
Acceptance Support v1 is READY, and the first real Human/Golden vertical has
now produced an exact Golden match through `DataGateway.scan()`. Its detailed
evidence is recorded in
[`HUMAN_GOLDEN_E2E_BYBIT_BTCUSDT_2024-01-15.md`](../integration/HUMAN_GOLDEN_E2E_BYBIT_BTCUSDT_2024-01-15.md).

This is a temporary integration gate pair, not a permanent architecture layer.
Both gates have passed. Their closure does not add a phase to the dependency
order below; it removes the temporary lockstep and restores the parallelism
that already existed.

## Phase A — controlled convergence — COMPLETE

The following remain unimplemented future capabilities and require their own
explicit scopes:

- full Candle runtime, which is no longer blocked by the Conformity Gate;
- Feature runtime and broader Representation expansion;
- broader producer verticals;
- live ingest and multi-venue runtime;
- L1/L2/L3/MBO runtime;
- unrelated consumer or producer vertical expansion.

The Human/Golden Bybit BTCUSDT vertical, Adversarial Acceptance, Candle Ordering
Compatibility and Final Conformity Gate Review are complete. All eight
ADR-0023 Decision §7 exit criteria passed.

## Operational follow-up outside the Gate critical path

**Server Access & Runtime Identity Hardening v1** is a separate operational
follow-up to restore or audit canonical server access, service identities,
filesystem ACLs, database roles and credential disposition. Human E2E
operator/bootstrap privileges are acceptance evidence only and do not define
production runtime authorization.

This follow-up was not a Gate blocker and remains independent of the completed
Conformity Implementation Gate. Human administration remains via SSH, local
services use canonical service identities, and future normal remote consumers
remain behind the planned Canonical API. No API runtime is introduced here.

## Phase B — post-Gate bidirectional expansion

The Conformity Implementation Gate has passed and the temporary cross-track
synchronization constraint is removed. Producer and Consumer development may
resume independently and concurrently, subject to normal ownership, dependency
direction, frozen contracts, architecture gates and explicit slice scopes.
The post-gate model is not architecture-unconstrained.

The mandatory cross-cutting structural checkpoint **Package Boundary / Modular
Monolith Foundation v1** is **COMPLETE**. It established bounded package
ownership and architecture tests inside the existing single-repository,
single-`src/` modular-monolith default without adding a product phase or
renumbering phases 0–15.

The mandatory **Legacy Capability Harvest Audit v1** is also **COMPLETE**. It
classified 31 legacy capabilities against current canonical semantics and
selected H01 — the pure diagonal / stacked imbalance core — as the first
recommended harvest slice. H14 DSR/PBO remains REVIEW on exact Evaluation
estimator/input semantics and does not block H01.

The legacy repository remains evidence only; harvesting never creates a runtime
dependency on it. Broad independent Producer/Consumer expansion is now
**UNLOCKED**, but every implementation capability still requires an explicit
bounded scope and must respect current ownership, dependency direction and
frozen contracts.

```text
Conformity Implementation Gate PASSED
            ↓
Package Boundary / Modular Monolith Foundation v1 COMPLETE
            ↓
Legacy Capability Harvest Audit v1 COMPLETE
            ↓
Broad independent Producer / Consumer expansion UNLOCKED
```

Selected next implementation candidate:

```text
H01 — Diagonal / Stacked Imbalance Core
```

H01 selection does not declare a complete Feature Engine or Footprint runtime.
Its pure quantitative legacy kernel is the accepted harvest candidate; canonical
grain, validated tick-grid input, temporal availability and FeatureDefinition
provenance remain requirements of the future bounded H01 implementation scope.

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
  Bybit first-vertical eligibility profile — READY through authoritative S13
        ↓
Publication Certification — COMPLETE
  Phase 1 SEAL → durable CLOSED registration
  Phase 2 CERTIFY → authoritative evaluation
  Phase 3 RECORD EVIDENCE → durable quality_reports
        ↓
Publication Eligibility Bridge — COMPLETE
  Phase 4 PUBLISH ELIGIBILITY → valid/degraded where frozen rules permit
  Phase 5 VERIFY → post-write eligibility/evidence verification
        ↓
Human vertical / Golden Bybit BTCUSDT E2E — PASS
        ↓
Adversarial Acceptance A1–A9 — PASS
        ↓
Candle Ordering Compatibility — PASS
        ↓
Final Conformity Gate Review — APPROVE
        ↓
Conformity Implementation Gate — PASSED
        ↓
Package Boundary / Modular Monolith Foundation v1 — COMPLETE
        ↓
Legacy Capability Harvest Audit v1 — COMPLETE
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
accepted (ADR-0021, CandleDefinition v1). The Conformity Implementation Gate has
passed, so Candle runtime is no longer blocked by that Gate. Candle runtime is
still unimplemented and remains a future capability requiring its own explicit
scope. Package Boundary / Modular Monolith Foundation v1 and Legacy Capability
Harvest Audit v1 are complete; independently scoped post-Gate expansion is now
unlocked. H01 — Diagonal / Stacked Imbalance Core — is the selected first
harvest implementation candidate.
