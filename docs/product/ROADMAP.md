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

The producer and consumer tracks below no longer expand independently and in
parallel. [ADR-0023](../decisions/ADR-0023-producer-consumer-conformity-gate-v1.md)
freezes **two** sequential, non-circular gates between the foundation
contracts already accepted (DataGateway v1, CandleDefinition v1, Declared
Coverage v1) and any further vertical expansion on either side:

```text
foundation contracts
        ↓
CONTRACT FREEZE GATE
  (documentation-level: docs/contracts/PRODUCER_CONSUMER_CONFORMITY.md
   independently reviewed and accepted)
        ↓
  unlocks: implementing the conformity slices (writer, manifest/coverage
  emission, certifier, bridge, bounded DataGateway read) — NOT broader
  vertical expansion yet
        ↓
CONFORMITY IMPLEMENTATION GATE
  (runtime-level: the conformity slices are implemented and pass the
   required tests, including the golden Bybit BTCUSDT vertical)
        ↓
  unlocks: producer AND consumer vertical expansion, AND Candle runtime
      ↙       ↘
producer      consumer
vertical      vertical
development   development
```

Before the Contract Freeze Gate passes, no conformity-slice implementation is
authorized. After it passes but before the Conformity Implementation Gate
passes, the conformity slices may be implemented, but producer vertical
expansion and consumer vertical expansion *beyond those slices* remain
suspended. Only Conformity Implementation Gate PASS reopens both. This is a
temporary integration gate pair, not a new permanent architecture layer:
passing both does not add a phase to the dependency order below, it only
re-opens the parallelism that already existed. See ADR-0023 for the exact
exit criteria of each gate.

## Parallel producer track (reopens after Conformity Implementation Gate PASS)

The consumer dependency track above remains canonical. In parallel, the Data
Plane producer track is:

```text
Market Data Ingest architecture and contracts
        ↓
Declared coverage contract
        ↓
Contract Freeze Gate  <-- current position
        ↓
Historical Trades publication slice (conformity slice: Parquet materializer,
  Bybit eligibility profile, manifest/coverage emission, certifier)
        ↓
Manifest → catalog publication bridge (conformity slice)
        ↓
Conformity Implementation Gate
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

Phase 2 (Representation foundation) has its semantic contract frozen and
accepted (ADR-0021, CandleDefinition v1). Candle runtime implementation is
explicitly postponed until the Conformity Implementation Gate passes, because
a historical candle runtime needs a bounded DataGateway read path and a
conforming publication chain that do not yet exist (ADR-0023).
