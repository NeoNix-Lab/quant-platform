# Quant Platform

[![Quant Platform integrity](https://github.com/NeoNix-Lab/quant-platform/actions/workflows/integrity.yml/badge.svg)](https://github.com/NeoNix-Lab/quant-platform/actions/workflows/integrity.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: Proprietary](https://img.shields.io/badge/license-Source--Available-red.svg)](LICENSE)

## What it is

**Quant Platform** is a historical-first, live-targeted quantitative research, simulation, and execution platform. Built with strict mathematical determinism, event-driven causality, and institutional accounting rigor, the platform ensures that research simulations, historical replays, and live execution share identical execution semantics and produce provably reproducible results.

---

## Architectural Principles & Core Invariants

The platform is designed around four non-negotiable architectural invariants:

1. **Bitwise Determinism & Monotonic Monolithic Time**:
   Simulations and replay engines advance time purely via monotonically non-decreasing timestamps (`Instant`). Given identical input data and configuration, repeated runs on any machine produce **bitwise-identical** order IDs, fill prices, fees, equity curves, and trace digests. Zero dependency on the host clock (`datetime.now()`).
2. **Point-in-Time Correctness & Availability Floor**:
   Strict causal anti-leakage invariants ($t_{\text{available}} \le t_{\text{decision}}$) are enforced at runtime. Strategy policies cannot observe any feature, indicator, or market event that would not have been available at decision time (ADR-0006, ADR-0031).
3. **Double-Entry Portfolio Accounting**:
   All monetary and quantity accounting is executed in exact `Decimal` arithmetic. Positions are tracked in hedge mode (independent long and short sides), with strict conservation of cash, position assets, fee expenses, and realized/unrealized PnL:
   $$\text{Equity}_t = \text{Cash}_t + \sum_{i} \text{PositionValue}_{i,t}$$
4. **DataGateway Seam Preservation**:
   Upper layers (Strategy, Execution, Portfolio, Replay) must consume market data exclusively through `DataGateway.scan()` and `DataGateway.read()`. Upper layers never open storage files or catalog tables directly (ADR-0019).

---

## High-Level Architecture

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

## Core Implemented Capabilities

- **Canonical Data Plane**: Frozen `trade-v1` schema, dataset and partition manifests, natural time partitions, storage root isolation, Bybit historical trade ingestion, and PostgreSQL catalog DDL.
- **Representations & Features**: Frozen `CandleDefinition v1`, `FootprintDefinition v1`, and `FeatureDefinition v1` semantic foundations with availability metadata.
- **Validation Engine**: Point-in-time train/test isolation, causal availability floors, purged and embargoed walk-forward cross-validation splits, and statistical robustness metrics.
- **Strategy & Policy Gates (`G01`–`G04`)**: Stateful `StrategySpec`, multi-signal combination policies, directional entry/exit policies, fixed-fraction position sizing, capital risk budgets (`max_drawdown_fraction`, position notional ceilings), 24/7 and exchange session calendars, and post-loss streak cooldown gating.
- **Unified Execution Engine (`H01`–`H03`)**: Order state machine (`PENDING_NEW` through `FILLED`/`CANCELED`), deterministic fill generation, explicit maker/taker fee schedules, synthetic slippage models, and intra-bar execution conflict and OCO resolvers.
- **Double-Entry Portfolio Ledger (`H04`)**: Double-entry journal lines, multi-asset hedge-mode position sides, exact cost basis tracking, transaction audit logs, and mark-to-market equity curves.
- **Deterministic Historical Replay Runtime (`H05`)**: End-to-end replay engine streaming from `DataGateway.scan()`, enforcing temporal causality chains ($t_{\text{event}} \le t_{\text{decision}} \le t_{\text{order}} \le t_{\text{fill}} \le t_{\text{ledger}}$), producing bitwise-identical `ReplayResult` artifacts and execution fingerprints.

---

## Repository Layout

```text
quant-platform/
├── db/            # PostgreSQL catalog schema, roles, and migration scripts
├── docs/          # Product roadmap, target architecture, core contracts, and 45+ ADRs
├── fixtures/      # Canonical JSON fixtures for schema validation and integration tests
├── infra/         # Container provisioning and disposable PostgreSQL test harnesses
├── schemas/       # Frozen JSON Schema definitions (trade-v1, manifest contracts)
├── src/           # Production source tree (quant_platform modular monolith)
│   └── quant_platform/
│       ├── access/       # DataGateway, catalog query provider, request models
│       ├── application/  # Ingestion pipelines, conformity runners, live bridges
│       ├── data/         # Domain models, dataset identities, Instant, TradeRecord
│       ├── execution/    # Orders, fills, fee schedules, slippage, conflict resolvers
│       ├── portfolio/    # Double-entry ledger, hedge positions, transaction journals
│       ├── replay/       # HistoricalReplayRuntime, ReplaySpec, ReplayResult
│       └── strategy/     # StrategySpec, policy gates, compose_decision, risk sizing
├── tests/         # Unit, boundary, property, and PostgreSQL integration test suites
└── tools/         # Workflow automation, preflight integrity, markdown link checkers
```

---

## Development & Verification

### Prerequisites

- Python 3.11 or higher
- Optional: Docker / PostgreSQL 17 (for catalog integration tests)

### Local Environment Setup

```bash
# Clone the repository
git clone https://github.com/NeoNix-Lab/quant-platform.git
cd quant-platform

# Create virtual environment and install test dependencies
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\Activate.ps1
pip install -r tests/requirements.txt
```

### Running Verification Checks

```bash
# 1. Run full repository preflight checks (syntax, boundaries, whitespace, governance)
python tools/workflow.py preflight

# 2. Run all local unit and regression test suites (73 suites, 0 failures)
python tools/run_tests.py

# 3. Verify all internal documentation links
python tools/check_markdown_links.py

# 4. Run targeted historical replay runtime tests
python -m unittest tests.test_replay_v1
```

---

## Documentation & Authority

- [Product Specification](docs/product/PRODUCT.md)
- [Capability Map](docs/product/CAPABILITY_MAP.md)
- [Product Roadmap](docs/product/ROADMAP.md)
- [Target Architecture](docs/architecture/TARGET_ARCHITECTURE.md)
- [Core Contracts](docs/contracts/CORE_CONTRACTS.md)
- [DataGateway Contract](docs/contracts/DATA_GATEWAY.md)
- [Market Data Ingest Architecture](docs/architecture/MARKET_DATA_INGEST.md)
- [Market Data Ingest Contracts](docs/contracts/MARKET_DATA_INGEST_CONTRACTS.md)
- [Storage Lifecycle](docs/architecture/STORAGE_LIFECYCLE.md)
- [Repository Synchronization](docs/engineering/REPOSITORY_SYNC.md)
- [Architecture Decision Records (ADRs)](docs/decisions/README.md)
- [Current Scope](SCOPE.md)

---

## Contributing & Security

- Contributions must follow the workflow defined in [CONTRIBUTING.md](CONTRIBUTING.md) and [AGENTS.md](AGENTS.md).
- To report a security vulnerability, please review our [Security Policy](SECURITY.md).

---

## License

This project is licensed under a **Source-Available Proprietary License**. See [LICENSE](LICENSE) for details.
