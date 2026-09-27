# Wave 4 Golden E2E — Deterministic Replay Reproducibility Proof

- **Status:** PASS
- **Milestone:** V7 (SCOPE.md Deterministic Replay Reproducibility)
- **Issue:** [#146](https://github.com/NeoNix-Lab/quant-platform/issues/146)
- **Date:** 2026-09-27
- **Dataset:** `canonical/trades/bybit/BTCUSDT/trade-v1`
- **Execution Target:** Homelab Production Catalog (`market_catalog`)

This document records the acceptance evidence for **Milestone V7** of Wave 4:
proving that two independent replay runs across the real canonical Bybit BTCUSDT
dataset produce bitwise-identical trace fingerprints (`trace_fingerprint`),
exercising the complete data access, strategy composition, execution admission,
fill simulation, and double-entry portfolio ledger accounting stack.

---

## Path Exercised

```text
PostgreSQL Catalog (market_catalog:5433)
        ↓
DataGateway.scan() (canonical trade-v1 Parquet partitions)
        ↓
OrderingProvider (bybit-trade-v1-exchange-ts-trade-id-v1)
        ↓
HistoricalReplayRuntime (H05)
        ↓
MinimalBreakoutFeatureProvider (Donchian-channel signals over trade stream)
        ↓
StrategyComposition (G02: signal combination, direction, target position)
        ↓
SessionPolicy (24/7 AlwaysOpenSessionPolicy for continuous crypto)
        ↓
CapitalRiskPolicy & FixedFractionSizingPolicy (G03: drawdown, exposure, lots)
        ↓
CooldownPolicy (G04: post-loss, max entries)
        ↓
ExecutionEngine.translate_intent (Order admission / refusal semantics)
        ↓
Simulated Exchange Fill (Taker market order, fee schedule)
        ↓
PortfolioLedger (H04: double-entry journals, cash, positions, realized PnL)
        ↓
ReplayResult (Equity curve, decisions, admissions, orders, fills, transactions)
        ↓
Trace Fingerprint (SHA-256 canonical serialization)
```

---

## Replay Specification

- **Dataset:** `canonical:trades:bybit:BTCUSDT:trade-v1`
- **Interval:** `2024-01-15T00:00:00Z` .. `2024-01-15T00:02:00Z`
- **Initial Capital:** `10000` USDT
- **Ordering Policy:** `bybit-trade-v1-exchange-ts-trade-id-v1`
- **Strategy Key:** `golden.e2e.breakout` (Semantic Version `1`)
- **Position Target:** `0.01` BTC
- **Lot / Min Size:** `0.001` BTC
- **Session Policy:** `AlwaysOpenSessionPolicy` (24/7 continuous session)
- **Fee Schedule:** `golden.fees` (Maker `0.0002`, Taker `0.00055`)

---

## Execution Evidence

Executed on `homelab` via `tools/golden_replay_e2e.py` against `/srv/marketdata/canonical/trades/bybit/BTCUSDT/trade-v1`:

```text
Interval: 2024-01-15T00:00:00Z .. 2024-01-15T00:02:00Z
Spec identity: replay-spec-v1:sha256:6523b88291a0bcbf355f2953406dc26cfba50498d0433e8f08e2f900e8dd23b6

Run 1 trace_fingerprint: replay-trace-v1:sha256:3b8a9ce8feefac4e6dedd91fd698a19bd2d5985735af1136b69894d8d943d20c
Run 2 trace_fingerprint: replay-trace-v1:sha256:3b8a9ce8feefac4e6dedd91fd698a19bd2d5985735af1136b69894d8d943d20c
Identical:               True

Orders:                   330
Fills:                    330
Final ledger book_equity: 9873.920986650
Final ledger identity:    portfolio-ledger-v1:sha256:2bcff3a8a0836dc41e0a1bf6b567a88a789ad37ba6a1fae85b5595340be1cc11
PASS: replay is bitwise-deterministic across independent runs.
```

---

## Acceptance Verification

1. **Deterministic Reproducibility (Milestone V7):** Both independent runs generated the exact same bitwise trace fingerprint:
   `replay-trace-v1:sha256:3b8a9ce8feefac4e6dedd91fd698a19bd2d5985735af1136b69894d8d943d20c`.
2. **Non-Vacuous Execution:** Exactly `330` orders and `330` fills were admitted, simulated, and accounted for in the double-entry portfolio ledger (`len(orders) > 0`).
3. **Ledger Integrity:** Book equity reconciled from `10000` USDT down to `9873.920986650` USDT after paying taker exchange fees and mark-to-market trading friction across 330 fills.
4. **Architectural Seams (ADR-0024):** All domain orchestration remains strictly inside `quant_platform.application.golden_replay`; the CLI `tools/golden_replay_e2e.py` operates solely as a thin user-facing entrypoint without touching raw domain subpackages.
