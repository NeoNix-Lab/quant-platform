# Quant Platform Documentation

Welcome to the technical documentation for **Quant Platform** — a historical-first, live-targeted quantitative research, simulation, and execution infrastructure designed around mathematical determinism, event-driven causality, and institutional accounting rigor.

---

## Architecture at a Glance

The platform is constructed as a modular monolith where each layer has strictly bounded responsibilities and explicit semantic contracts:

```text
Market Data Ingest (Bybit Trades / L2)
         ↓
Data Plane & Storage Roots (Natural Partitioning, Parquet, Manifest Hashes)
         ↓
PostgreSQL Catalog & Lineage Tracking (DDL, DataGateway Boundary)
         ↓
Representations & Feature Engine (Candles D03, Footprints D04, Features E04/E06)
         ↓
Validation & Walk-Forward Engine (Causal Floor, Purged & Embargoed Folds)
         ↓
Strategy Policies & Composition (G01 StrategySpec, G02 Signals, G04 Session/Cooldown, G03 Risk/Sizing)
         ↓
DecisionIntent (Learner-Agnostic, ADR-0009)
         ↓
Unified Execution Engine (H01 Order State Machine, H02 Fees/Slippage, H03 OCO/Conflict Resolver)
         ↓
Historical Replay Runtime (H05 Deterministic ReplayEngine, Trace Fingerprint)
         ↓
Double-Entry Portfolio Ledger (H04 Ledger Transactions, Realized/Unrealized PnL, Mark-to-Market Equity)
```

---

## Documentation Sections

| Section | Focus & Contents | Key Documents |
| :--- | :--- | :--- |
| **[Product & Roadmap](product/PRODUCT.md)** | Product vision, capability taxonomy, and staged execution roadmap | [Product](product/PRODUCT.md) · [Roadmap](product/ROADMAP.md) · [Capability Map](product/CAPABILITY_MAP.md) |
| **[Target Architecture](architecture/TARGET_ARCHITECTURE.md)** | System topology, storage lifecycle, and market data ingest architecture | [Target Architecture](architecture/TARGET_ARCHITECTURE.md) · [Storage Lifecycle](architecture/STORAGE_LIFECYCLE.md) · [Ingest Architecture](architecture/MARKET_DATA_INGEST.md) |
| **[Core Contracts](contracts/CORE_CONTRACTS.md)** | Frozen semantic contracts (§1 through §33), DataGateway interface, and conformity gates | [Core Contracts](contracts/CORE_CONTRACTS.md) · [DataGateway](contracts/DATA_GATEWAY.md) · [CandleDefinition](contracts/CANDLE_DEFINITION.md) |
| **[Architecture Decision Records](decisions/README.md)** | Chronological immutable architectural decisions (ADR-0001 through ADR-0046) | [ADR Index](decisions/README.md) |
| **[Engineering & Operations](engineering/REPOSITORY_SYNC.md)** | Definition of Done, repository synchronization, and deployment procedures | [Definition of Done](engineering/DEFINITION_OF_DONE.md) · [Repository Sync](engineering/REPOSITORY_SYNC.md) |
| **[Integration & Proofs](integration/WAVE4_GOLDEN_E2E_DETERMINISTIC_REPLAY.md)** | End-to-end acceptance proofs and reproducible golden replay evidence | [Wave 4 Golden Replay Proof](integration/WAVE4_GOLDEN_E2E_DETERMINISTIC_REPLAY.md) |

---

## Core Non-Negotiable Invariants

1. **Bitwise Determinism & Monotonic Time**:
   All simulations advance time exclusively via simulated event timestamps (`Instant`). Replays run on fixture market data produce bitwise-identical `ReplayResult` artifacts, order logs, fill sequences, and SHA-256 trace fingerprints.

2. **Causal Availability Floor**:
   The platform enforces point-in-time correctness:
   $$t_{\text{available}} \le t_{\text{decision}}$$
   Any attempt to access data with a future availability timestamp fails closed with a fatal exception (ADR-0006, ADR-0031).

3. **Double-Entry Accounting Conservation**:
   All financial math is executed in exact `Decimal` arithmetic. Positions are accounted in hedge mode, strictly conserving balance sheets:
   $$\text{Equity}_t = \text{Cash}_t + \sum_{i} \text{PositionValue}_{i,t}$$

4. **DataGateway Seam Isolation**:
   Upper layers (Strategy, Execution, Portfolio, Replay) must consume market data exclusively through `DataGateway.scan()` and `DataGateway.read()`, never directly accessing storage files or raw database tables (ADR-0019).

---

## Machine Learning & RL Integration

Quant Platform is engineered to provide institutional foundations for statistical learning and artificial intelligence in quantitative trading:

* **Zero Lookahead Bias**: Mathematically enforced causal floors ($t_{\text{available}} \le t_{\text{decision}}$) protect training features from accidental future leakage.
* **Purged & Embargoed Cross-Validation**: Native walk-forward cross-validation folds with combinatorial embargo intervals ([ADR-0031](decisions/ADR-0031-availability-purge-embargo-v1.md)) and Deflated Sharpe Ratio / PBO statistical evaluations ([ADR-0037](decisions/ADR-0037-dsr-pbo-robust-comparison-v1.md)).
* **Learner-Agnostic Decision Interface**: `DecisionIntent` ([ADR-0009](decisions/ADR-0009-policy-decision-intent.md)) allows arbitrary predictive models (Gradient Boosting, PyTorch Deep Learning, Reinforcement Learning agents) to emit trade signals without entangling execution state machines or accounting rules.
* **Sister Research Ecosystem**: Recurrent neural networks, sequence predictors, and deep learning experiments sharing this algorithmic foundation are explored in our companion repository [**Rnn_V0_1**](https://github.com/NeoNix-Lab/Rnn_V0_1).

---

## Research Frontiers & Community RFCs

Beyond our frozen core architecture, we actively explore cutting-edge quantitative ideas with researchers and the open-source community:

* [**RFC #182: Live Execution Adapters**](https://github.com/NeoNix-Lab/quant-platform/discussions/182) — Evaluating CCXT Pro vs. Native Async WebSockets (Bybit / Interactive Brokers) within our deterministic state machine.
* [**RFC #183: Causal Reinforcement Learning & Reward Function Design**](https://github.com/NeoNix-Lab/quant-platform/discussions/183) — Formulating cost-penalized differential Sharpe rewards and risk budgets in `DecisionIntent`.
* [**RFC #184: Interactive Order Flow & Footprint Visualizer**](https://github.com/NeoNix-Lab/quant-platform/discussions/184) — Designing a terminal TUI vs. lightweight web UI for cluster delta footprints and replay stepping.
* [**RFC #185: System One Decision Models (TypeSafe Jev)**](https://github.com/NeoNix-Lab/quant-platform/discussions/185) — Evaluating fast typed inference models against exchange fee hurdles and adverse-selection gating.
* [**RFC #186: JEPA World Models for Order Flow & L3/MBO Data**](https://github.com/NeoNix-Lab/quant-platform/discussions/186) — Self-supervised representation learning (Fin-JEPA) on unlabelled high-frequency market events.

*Have an idea or research proposal? Join the debate in [**Discussions ➔ Ideas**](https://github.com/NeoNix-Lab/quant-platform/discussions/categories/ideas)!*
