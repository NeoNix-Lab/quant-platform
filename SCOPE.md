# Scope: Wave 4 — Strategy & Deterministic Replay v1

Status: **ACTIVE**

Scope kind: **implementation, decision-gate resolution and validation scope for Wave 4**.

## Objective

Build and validate the canonical **Strategy & Deterministic Replay** vertical (Vertical Milestone **V7**) on the `implement/wave-4` integration branch, establishing the bridge between validated quantitative research and stateful economic execution:

```text
Canonical Market Data (DataGateway B02 / D03)
                     ↓
Derived Features & Validated Labels (E04 / E06 / F07)
                     ↓
StrategySpec & Policies (G01 / G02 / G03 / G04)
                     ↓
DecisionIntent (ADR-0009)
                     ↓
Execution Engine & Cost/Conflict Model (H01 / H02 / H03)
                     ↓
Orders / Realized Fills (ADR-0012)
                     ↓
Portfolio & Accounting Ledger (H04)
                     ↓
Deterministic Historical Replay Runtime (H05)
```

The objective is to enable stateful trading strategies to evaluate canonical inputs, generate learner-agnostic `DecisionIntent`, execute against a unified `ExecutionEngine` under explicit fee and slippage models, record every economic transaction in a double-entry `Portfolio/Ledger`, and produce provably reproducible, bitwise-identical results across repeated historical replays.

This scope resolves Decision Gate **`DG-F`** (Strategy & Execution Semantics) and delivers the implementation of atoms `G01`–`G04` and `H01`–`H05`.

It does **not** include live exchange order execution, broker adapters, live consumer cursor `B06`, multi-asset execution `H06`, supervised ML model training (`Wave 5`), API transport (`J02`), thin clients (`J04`–`J06`), or paper/live trading mode (`J07` / `J08`).

---

## Authority

Start from:

- `AGENTS.md`
- `README.md`
- `docs/product/PRODUCT.md`
- `docs/product/CAPABILITY_MAP.md`
- `docs/product/CAPABILITY_DAG.md`
- `docs/product/ROADMAP.md`
- `docs/architecture/TARGET_ARCHITECTURE.md`
- `docs/contracts/CORE_CONTRACTS.md` (Sections 21–27)
- `docs/architecture/OPEN_DECISIONS.md` (`DG-F` family)
- `docs/decisions/ADR-0007-research-strategy-separation.md`
- `docs/decisions/ADR-0009-policy-decision-intent.md`
- `docs/decisions/ADR-0012-unified-execution.md`
- `docs/decisions/ADR-0013-historical-live-semantics.md`
- `docs/decisions/ADR-0024-package-boundary-modular-monolith-v1.md`
- `docs/decisions/ADR-0036-labels-censoring-lockbox-v1.md`
- `docs/decisions/ADR-0038-outcome-v1-semantic-authority.md`
- `docs/decisions/ADR-0043-live-ingest-server-composition-v1.md`
- `docs/decisions/ADR-0044-live-ingest-long-gap-remediation-v1.md`

Accepted ADRs and frozen contracts remain normative semantic authority. This scope organizes the implementation work for Wave 4 without weakening or reinterpreting existing contracts.

---

## Baseline and Credited State

Authoritative baseline for this branch:

```text
implement/wave-4 @ 67fafb005c0b79df5efa042c81bf52077c4f0d53 (Merge PR #150 into main)
```

Credit, do not reimplement or re-prove absent invalidating evidence:

```text
Wave 0  Architecture foundation, schemas, catalog bootstrap, modular monolith (ADR-0024)   COMPLETE
Wave 1  DataGateway bounded scan (B02), CandleDefinition v1 (D02), historical candles (D03),
        walk-forward (F05), capacity (K04), config convergence (C05), ASS-01/02/03         COMPLETE
Wave 2  FootprintDefinition v1 (D06), FeatureDefinition v1 (E02), FeatureArtifact v1 (E04),
        canonical H01 integration (E06)                                                    COMPLETE
Wave 3  Research & Validation complete (F01–F08): HypothesisSpec (F01), EventSpec (F02),
        OutcomeSpec/Outcome v1 (F03/ADR-0038), sweeps (F04), walk-forward (F05),
        availability/purge/embargo (F06/ADR-0031), labels/censoring/lockbox (F07/ADR-0036),
        DSR/PBO robust comparison (F08/ADR-0037)                                           COMPLETE
Wave 6  Live Ingest Server Production Readiness v1 (PR #123, ADR-0043, ADR-0044)           COMPLETE
Tools   Branching strategy, pre-push guardrail, workflow CLI (PR #148, #149, #150)         COMPLETE
Data    trade-v1, manifests, catalog DDL, Parquet materialization, certification, publication   COMPLETE
Ops     K02 identity, K03 observability, K05 pressure, K06 source protection, K08 backup,
        K10 checkpoint/recovery v1                                                         COMPLETE
```

---

## In-Scope Capability Inventory

| ID | Capability | Owner | Requires | Unlocks | Decision State | Target Impl State | Acceptance / Authority |
| :--- | :--- | :--- | :--- | :--- | :---: | :---: | :--- |
| **G01** | `StrategySpec / DecisionIntent v1` | Strategy | `F07` | `G02`, `H01` | RESOLVED | COMPLETE | Immutable specification; inputs strictly respect availability floor; ADR-0009 |
| **G02** | `Policy composition v1` | Strategy | `G01` | `G03`, `G04` | RESOLVED | COMPLETE | Deterministic evaluation of entry, exit and position policies |
| **G03** | `Risk / sizing v1` | Strategy | `G01`, `G02` | `H01` | RESOLVED | COMPLETE | Reproducible capital allocation, max drawdown protection, position sizing |
| **G04** | `Session / cooldown semantics v1` | Strategy | `G02`, `F06` | `H01`, `H03` | **OPEN_BLOCKING (DG-F)** | COMPLETE | Trading calendar, session schedules, cooldown states, timezone/DST handling |
| **H01** | `Order / Fill lifecycle v1` | Execution | `G03`, `G04` | `H02`, `H04` | RESOLVED | COMPLETE | Explicit state machine; illegal transitions rejected; reproducible order IDs |
| **H02** | `Cost / synthetic-fill model v1` | Execution | `H01` | `H04`, `H05` | RESOLVED | COMPLETE | Parameterized fee schedule, deterministic slippage model, provenance |
| **H03** | `Execution conflict model v1` | Execution | `H01`, `G04` | `H04`, `H05` | **OPEN_BLOCKING (DG-F)** | COMPLETE | Deterministic resolution of same-bar conflicts, intra-bar ambiguity, OCO |
| **H04** | `Portfolio / ledger v1` | Portfolio | `H01`, `H02`, `H03` | `H05`, `I03` | RESOLVED | COMPLETE | Double-entry accounting ledger; equity = cash + positions; exact PnL conservation |
| **H05** | `Deterministic replay v1` | Execution | `H02`, `H04` | `J07`, `I03` | RESOLVED | COMPLETE | Historical replay runtime binding dataset, strategy, and execution deterministically |

---

## Decision Gate: DG-F (Strategy & Execution Semantics)

Wave 4 activates and must resolve Decision Gate **`DG-F`** before dependent execution implementation is authorized:

### DG-F.1: Session & Cooldown Semantics (`G04`)
* **Session calendars**: explicit trading sessions, exchange maintenance windows, weekend boundaries, and trading day rollover.
* **Cooldown policy**: deterministic state machine for cool-down periods (post-loss cooldown, maximum consecutive losses cooldown, intra-day frequency limit).
* **Timezone / DST integrity**: pure temporal evaluation in UTC microseconds without host system clock dependency.

### DG-F.2: Execution Conflict Model (`H03`)
* **Same-bar conflict resolution**: deterministic precedence rules when multiple orders trigger within the same bar or event bucket (e.g. stop-loss priority over take-profit, or conservative worst-case fill).
* **Intra-bar ambiguity**: explicit policy for price path uncertainty (conservative assumption vs explicit lower-timeframe/trade-tick resolution).
* **Order cancellation & OCO**: deterministic One-Cancels-Other linking and cancellation cascading.
* **Partial fills**: deterministic fill ratio rules under liquidity and synthetic fill constraints.

---

## Active Path (Execution Sequencing)

The repository rule of **one bounded mutation slice at a time** strictly governs execution:

```text
1. Decision Gate DG-F.1 (ADR-0045) — Session & Cooldown Semantics Design (G04)
   Formalize session calendar, cooldown state machine, and timezone invariants.
        ↓
2. Decision Gate DG-F.2 (ADR-0046) — Execution Conflict Model Design (H03)
   Formalize same-bar conflict resolution, intra-bar precedence, and fill determinism.
        ↓
3. Strategy Core Implementation Slice (G01 & G02)
   Implement StrategySpec, Policy composition, and DecisionIntent contract/schema.
   Prove temporal availability floor (no lookahead bias).
        ↓
4. Risk, Sizing & Session Implementation Slice (G03 & G04)
   Implement risk budgets, position sizing, session filtering, and cooldown runtime.
        ↓
5. Order & Fill Lifecycle Implementation Slice (H01 & H02)
   Implement Order/Fill state machines, fee schedules, and synthetic slippage models.
        ↓
6. Execution Conflict & Portfolio Ledger Implementation Slice (H03 & H04)
   Implement same-bar conflict resolver, double-entry accounting ledger, and equity tracking.
        ↓
7. Deterministic Replay Engine Implementation Slice (H05)
   Implement historical replay runtime composing DataGateway, Features, Strategy, and Ledger.
        ↓
8. Wave 4 Human / Golden End-to-End Replay Proof (Milestone V7)
   Execute and record deterministic replay proof on canonical Bybit BTCUSDT dataset.
   Prove identical ledger and trade traces across independent runs.
        ↓
9. Wave 4 Governance Closeout
   Reconcile CAPABILITY_DAG.md, CAPABILITY_MAP.md, ROADMAP.md, and OPEN_DECISIONS.md.
```

---

## Acceptance Criteria

`Wave 4 — Strategy & Deterministic Replay v1` is complete only when all of the following are observably true:

1. **Temporal Correctness**: `DecisionIntent` generation rejects any feature, label, or market input whose availability timestamp is later than `decision_time`.
2. **Learner Agnosticism**: `DecisionIntent` conforms strictly to ADR-0009 and is identical in contract regardless of whether produced by rules, heuristics, or future models.
3. **Execution Invariants**: `Order` and `Fill` state transitions follow an explicit, fail-closed state machine; illegal state transitions (e.g. fill after cancel, duplicate fill) raise explicit domain errors.
4. **Economic Accounting Conservation**: `Portfolio/Ledger` maintains strict double-entry accounting invariants:
   $$\text{Equity}_t = \text{Cash}_t + \sum \text{PositionValue}_{i,t}$$
   $$\Delta \text{Equity} = \text{RealizedPnL} + \Delta \text{UnrealizedPnL} - \text{Fees}$$
5. **Deterministic Replay Reproducibility**: Given an identical tuple $(\text{Dataset}, \text{StrategySpec}, \text{ExecutionSpec})$, the replay runtime produces identical order IDs, fill timestamps, prices, and final ledger state across repeated executions and platforms.
6. **DataGateway Seam Preservation**: The strategy and replay engines consume canonical market data exclusively via `DataGateway.scan()` and do not bypass the access layer to inspect Parquet files directly.
7. **Architectural Separation**: Quantitative business logic remains in domain and application modules; no strategy or execution logic resides in API or client packages.
8. **Verification Gate**: The repository verification gate (`tools/run_tests.py` and `tools/check_markdown_links.py`) passes with 100% green status.
9. **Governance Reconciliation**: Governance documents are reconciled to record Wave 4 as `COMPLETE` and Milestone `V7` as `COMPLETE`.

---

## Out of Scope

Do not pull into Wave 4 unless an authorized contract evolution explicitly requires it:

- Live broker connection or exchange order execution (belongs to live execution phase).
- Live consumer cursor `B06` (remains independent Data Access atom).
- Multi-asset portfolio rebalancing / execution `H06` (deferred until concrete multi-asset scope).
- Supervised ML training pipelines `I04`/`I05` (Wave 5).
- Strategic RL (`I06`) or Execution RL (`I07`) (Wave 11 / Wave 12).
- API transport `J02` and UI/CLI clients `J04`–`J06` (Wave 7).
- Paper / shadow trading runtime `J07` and live product mode `J08` (Wave 8).
- Deletion / retention authority `K09` or tier relocation `K07`.

---

## Stop / Escalation Conditions

Stop and report rather than implement if:

- A proposed strategy or replay mechanism requires information not causally available at decision time.
- An execution or ledger calculation produces non-deterministic floating-point discrepancies across platforms.
- Satisfying replay requirements requires bypassing `DataGateway` or mutating frozen data-plane contracts (`trade-v1`, `CandleDefinition v1`).
- The execution engine cannot achieve conflict resolution without introducing non-reproducible arbitrary race conditions.
