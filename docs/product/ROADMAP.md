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

## Parallel producer track

The consumer dependency track above remains canonical. In parallel, the Data
Plane producer track is:

```text
Market Data Ingest architecture and contracts
        ↓
Historical Trades publication slice
        ↓
Manifest → catalog publication bridge
        ↓
Capacity monitoring foundation
        ↓
Safe storage placement and relocation
        ↓
Backup / restore foundation
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
capacity monitoring, pressure behavior, source-data protection, and recovery.

Provenance and identity primitives are required as soon as their entities are
implemented; they do not wait for Phase 8. Research may continue across
phases. Each phase starts only through an explicit scope.

Repository Synchronization & Integrity Foundation v1 is the infrastructure
gate between the reviewed DataGateway Contract v1 and DataGateway runtime
implementation. It does not add a product capability or renumber the phases.
