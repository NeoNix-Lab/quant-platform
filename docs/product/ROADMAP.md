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
**IN PROGRESS / PARTIAL**. Human/Golden Bybit BTCUSDT E2E has passed, while
adversarial acceptance, Candle ordering compatibility and the final Gate review
remain open.

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
governance closeout → branch review / CI → merge
        ↓
from authoritative main:
Adversarial Acceptance + Candle Ordering Compatibility
        ↓
Final Conformity Gate Review
        ↓
CONFORMITY IMPLEMENTATION GATE — PASS
        ↓
Package Boundary → Legacy Capability Harvest → broader expansion
```

The completed slices are Shared Semantic Primitives v1, Canonical Parquet
Materializer v1, Manifest + Coverage Emission v1, Bounded DataGateway finite
read v1, Publication Certification runtime v1 (S13 Phases 1–3), and
Publication Eligibility Bridge v1 (S14 / S13.5 Phases 4–5). Golden Conformity
Acceptance Support v1 is READY, and the first real Human/Golden vertical has
now produced an exact Golden match through `DataGateway.scan()`. Its detailed
evidence is recorded in
[`HUMAN_GOLDEN_E2E_BYBIT_BTCUSDT_2024-01-15.md`](../integration/HUMAN_GOLDEN_E2E_BYBIT_BTCUSDT_2024-01-15.md).

Merging the Human E2E branch records this completed milestone. It does not
promote the Conformity Implementation Gate and does not authorize post-gate
work.

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

The Human/Golden Bybit BTCUSDT vertical is complete. The current branch proceeds
through governance closeout, final review and CI before merge. From authoritative
`main`, the remaining gate path is **Adversarial Acceptance**, **Candle Ordering
Compatibility**, then the **Final Conformity Gate Review**.

## Operational follow-up outside the Gate critical path

**Server Access & Runtime Identity Hardening v1** is a separate operational
follow-up to restore or audit canonical server access, service identities,
filesystem ACLs, database roles and credential disposition. Human E2E
operator/bootstrap privileges are acceptance evidence only and do not define
production runtime authorization.

This follow-up does not block the Human E2E merge and does not open or close the
Conformity Implementation Gate. Human administration remains via SSH, local
services use canonical service identities, and future normal remote consumers
remain behind the planned Canonical API. No API runtime is introduced here.

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
Governance closeout → branch review / CI → MERGE
        ↓
From main: Adversarial acceptance + Candle ordering proof
        ↓
Final Conformity Gate Review
        ↓
Conformity Implementation Gate — PASS
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
the bounded historical DataGateway read, Canonical Parquet Materializer,
Manifest + Coverage Emission, authoritative Publication Certification and S14
Publication Eligibility Bridge are implemented, and the Human Golden proof is
complete. Adversarial acceptance, Candle ordering compatibility and the final
Gate review still require completion.
Candle runtime remains blocked until the complete seam is proven and the
Conformity Implementation Gate passes.
