# PRODUCT.md

**Project:** Quant Platform  
**Status:** DRAFT v0.1 — product baseline for review  
**Target:** complete quantitative research and trading platform, historical-first and live-ready  
**Canonical codebase:** `quant-platform`  
**Legacy reference:** `ml_core` is read-only evidence/reference, not architectural authority

---

## 1. Product mission

`quant-platform` is the authoritative codebase for a complete quantitative research and trading workbench.

The product must support the full path from canonical market data to live-capable decision and execution workflows:

1. ingest and canonicalize market data;
2. derive reusable market features;
3. formulate and inspect hypotheses;
4. measure conditional market outcomes;
5. define labels where learning is required;
6. test stateful trading strategies;
7. train and evaluate supervised and reinforcement-learning models;
8. execute the same decision semantics in historical replay, paper/shadow and live modes;
9. persist every study, run and artifact with reproducible provenance;
10. expose all canonical capabilities through an API consumed by App UI, TUI and CLI.

The system is not centered on a particular model class, strategy style or UI.  
The central object is a reproducible quantitative experiment over temporally correct market data.

---

## 2. Product principles

### 2.1 One authoritative product codebase

`quant-platform` is the canonical product repository.

Legacy repositories may be inspected for algorithms, tests, semantics and historical decisions, but:

- no backward compatibility is required;
- legacy modules are not architectural authority;
- modules are not copied wholesale;
- a legacy capability enters the canonical runtime only after semantic review and verification.

### 2.2 API-first application architecture

The canonical application boundary is the API.

```text
App UI ─┐
TUI ────┼──► API ─► Application Services ─► Domain Engines
CLI ────┘
```

App UI, TUI and CLI must not implement quantitative business logic.

### 2.3 Data-local heavy computation

Large market-data transformations and feature materializations should execute close to the canonical data on the server.

Git contains code, schemas, tests, fixtures and definitions.  
Large canonical datasets, feature datasets, caches and research artifacts do not belong in Git.

### 2.4 Temporal correctness is a domain invariant

The platform must distinguish, where applicable:

- event time;
- observation time;
- feature availability time;
- signal availability time;
- order submission time;
- fill time.

A backtest or live policy must never consume information before it is available.

### 2.5 Historical replay and live share semantics

The target architecture must not treat historical research and live trading as separate universes.

The same strategy/policy semantics should operate over different market-event sources:

```text
Strategy / Policy
       │
       ▼
DecisionIntent
       │
       ▼
Execution Engine
       │
       ├── Historical Market Replay
       ├── Paper / Shadow
       └── Live Market
```

---

## 3. Market-data domains

The platform must support these market-data families as distinct contracts:

- Trades
- L1 / top-of-book
- Footprint / price-level trade aggregation
- L2 / aggregated depth
- L3 / order-level events, where the venue/feed provides them

### 3.1 Terminology rule

`L1` means **market-data Level 1 / top-of-book**.

Do not use `L1` to mean “first-level derived feature”.

Reusable derived observables such as delta, aggressive volume and imbalance are called **primitive features** or **base derived features**.

---

## 4. Candles are first-class derived representations

Candles are official reproducible representations, not UI-only objects.

A candle definition must identify at minimum:

- source dataset;
- interval;
- alignment;
- interval boundary semantics;
- timestamp convention;
- empty-interval policy;
- late-event policy;
- partial/closed state;
- availability semantics.

The platform must support ordinary OHLCV candles and enriched candle-grain features.

Examples:

- OHLCV;
- trade count;
- VWAP;
- aggressive buy/sell volume;
- delta;
- imbalance;
- intensity;
- L1 summary features;
- footprint features;
- L2/L3 summary features when valid for the source data.

A final closed candle and an in-progress partial candle are semantically different objects.

---

## 5. Unified Feature Engine

All derived market observables belong to one Feature Engine.

Provider families include:

- Trades
- L1
- Footprint
- L2
- L3
- Technical indicators
- Market/session/context
- Custom feature providers

Order-flow is not a peer engine. It is represented through feature providers using trade, footprint, L1, L2 and L3 data.

Examples of primitive trade-derived features:

- aggressive buy volume;
- aggressive sell volume;
- delta;
- total volume;
- trade count;
- VWAP;
- buy/sell ratio;
- imbalance;
- trade intensity;
- volume intensity;
- price returns;
- range/body/wick descriptors at candle grain.

The Feature Engine must support:

- deterministic definitions;
- explicit input requirements;
- explicit grain;
- explicit temporal availability;
- reproducible implementation identity;
- lineage/provenance;
- ephemeral computation;
- cacheable computation;
- official materialization.

---

## 6. Separation of quantitative concepts

The platform must preserve these distinctions:

```text
Canonical Data
      ↓
Features
      ↓
Hypothesis / Event Definition
      ↓
Event Study / Outcomes
      ↓
Signal or Label
      ↓
Policy / Strategy
      ↓
Execution
```

Specifically:

- a feature is not a hypothesis;
- a hypothesis is not a label;
- an event is not automatically a signal;
- a forward return is not a backtest;
- a label is not necessarily a trading action;
- a policy is not the execution engine.

---

## 7. Research Engine

The Research Engine answers:

> Is there a reproducible conditional market phenomenon?

It must support:

- declarative hypothesis/recipe definitions;
- event detection;
- event enumeration;
- event explanation/inspection;
- conditional outcome analysis;
- regime breakdown;
- temporal stability;
- out-of-sample validation;
- parameter sweeps with trial accounting.

The Research Engine must not be tied to a UI.

---

## 8. Outcome Engine

The Outcome Engine describes what happened after a defined observation/event time.

Outcomes may include:

- forward returns at multiple horizons;
- MFE;
- MAE;
- time to MFE/MAE;
- upper/lower barrier hits;
- first-barrier hit;
- time-to-event;
- path-dependent drawdown;
- realized volatility;
- net outcome under an ExecutionSpec;
- other path descriptors.

Outcomes are not automatically labels.

---

## 9. Label Engine

The Label Engine transforms reproducible outcomes or policy simulations into learning targets.

At least two label families are required.

### 9.1 Outcome labels

Examples:

- direction;
- future return bucket;
- triple barrier;
- MFE/MAE class;
- volatility regime;
- time-to-event.

### 9.2 Policy-derived labels

These quantify the quality of a decision under an explicit trading/execution logic.

Examples:

- GOOD_LONG / BAD_LONG / NEUTRAL;
- target-before-stop;
- continuous trade-quality score;
- long/short/flat utility estimates.

Policy-derived labels must fingerprint the strategy/execution assumptions used to create them.

---

## 10. Stateful Strategy and Policy layer

A real strategy is stateful.

A StrategySpec may include:

- EntryPolicy
- ExitPolicy
- PositionPolicy
- SizingPolicy
- RiskPolicy
- SessionPolicy
- SignalCombinationPolicy
- ExecutionPolicy

The system must support rule-based, supervised-ML-assisted and RL-derived policies without requiring different backtest semantics for each.

---

## 11. Supervised ML and RL

Supervised ML and RL are complementary.

### 11.1 Supervised learning

Typical role:

> Estimate what is likely to happen.

Examples:

- probability target before stop;
- expected return;
- expected MFE/MAE;
- regime probability;
- trade-quality score.

### 11.2 Strategic RL

Typical role:

> Given market state, current portfolio state, predictions and constraints, what action or target position should be selected?

Possible actions:

- flat;
- long;
- short;
- increase;
- reduce;
- exit;
- position sizing.

### 11.3 Execution RL

Execution RL is a separate task from strategic RL.

It answers:

> Given a desired trade/position change, how should orders be managed?

Possible actions:

- market/limit;
- price selection;
- order size;
- wait;
- cancel;
- replace;
- aggressiveness.

State may include L1/L2/L3, spread, queue, liquidity, inventory and remaining quantity.

---

## 12. Learner-agnostic decision boundary

Rules, supervised models and RL policies must converge on a common decision contract.

Conceptually:

```text
DecisionIntent
- decision_time
- instrument
- target_position / desired exposure
- direction
- size or risk budget
- confidence
- urgency
- provenance
```

The execution layer must not need to know whether the DecisionIntent originated from rules, supervised ML or RL.

---

## 13. Execution and backtesting

A forward-return study is not a strategy backtest.

A canonical backtest must simulate stateful trading including, where configured:

- entries;
- exits;
- position state;
- sizing;
- stops;
- targets;
- partial exits;
- trailing;
- cooldowns;
- fees;
- slippage;
- latency;
- order/fill semantics;
- portfolio/risk constraints.

All strategy families must converge on one canonical execution semantics.

---

## 14. Evaluation domains

The platform must distinguish at least three evaluation families.

### 14.1 Research statistics

Used to test whether an edge exists:

- event count and coverage;
- conditional-return distributions;
- effect size;
- confidence intervals;
- MFE/MAE;
- regime stability;
- temporal stability;
- OOS fold consistency;
- multiple-testing controls for large sweeps.

### 14.2 Strategy statistics

Used to test economic viability:

- expectancy;
- profit factor;
- Sharpe/Sortino where appropriate;
- drawdown;
- recovery;
- turnover;
- exposure;
- win/loss distributions;
- R distributions;
- tail risk;
- holding time;
- capacity;
- fee/slippage sensitivity.

### 14.3 Learning statistics

Used to test generalization.

Supervised examples:

- calibration;
- classification/regression metrics where appropriate;
- OOS economic value;
- trial accounting;
- DSR/PBO where applicable.

RL examples:

- policy return;
- policy stability;
- action distribution;
- seed sensitivity;
- regime robustness;
- OOS degradation.

---

## 15. Experiment system

Every relevant result must be reproducible.

The Experiment System must identify and persist:

- dataset identity/provenance;
- feature definitions;
- hypothesis/recipe;
- outcome/label definitions;
- validation configuration;
- learner/policy configuration;
- strategy/risk configuration;
- execution configuration;
- code identity;
- runtime/environment identity where relevant;
- metrics;
- artifacts.

Core concepts:

- Study
- Trial
- Run
- Artifact

---

## 16. User interfaces

### 16.1 TUI

Optimized for:

- dataset selection;
- recipes/hypotheses;
- feature inspection;
- event studies;
- sweeps;
- validation;
- backtests;
- runs;
- comparison.

### 16.2 App UI

Optimized for visual research and operational control:

- candlestick charts;
- volume/delta;
- footprint;
- L1/L2 overlays;
- event annotations;
- entries/exits;
- labels;
- predictions;
- live/paper/shadow observation;
- run comparison.

### 16.3 CLI

Optimized for automation, scripting, testing and reproducible batch workflows.

All three consume the canonical API.

---

## 17. Live target

The final product target includes:

- live market ingestion;
- incremental candle/feature computation;
- paper trading;
- shadow trading/reconciliation;
- live policy execution;
- risk controls;
- order/fill reconciliation;
- edge-decay monitoring;
- operational monitoring and recovery.

Live support is an architectural target from the beginning even when early implementation cycles are historical-first.

---

## 18. Out of scope as architectural requirements

The platform does **not** require:

- backward compatibility with the legacy runtime;
- preservation of legacy APIs;
- a generic DAG framework;
- separate backtest engines for Rules/ML/RL;
- quantitative business logic inside App/TUI/CLI;
- one dataset materialization for every experiment.

---

## 19. Product success condition

The platform is successful when a user can move reproducibly from:

```text
canonical market data
→ primitive features
→ hypothesis
→ outcome/event study
→ validation
→ strategy/policy
→ execution/backtest
→ experiment comparison
→ paper/shadow/live
```

while preserving temporal correctness, provenance and identical core semantics across interfaces.
