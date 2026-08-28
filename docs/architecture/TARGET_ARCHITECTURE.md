# TARGET_ARCHITECTURE.md

**Status:** DRAFT v0.1  
**Architecture target:** historical-first, live-ready quantitative research and trading platform

---

## 1. Authority and repository boundary

The authoritative product codebase is `quant-platform`.

Legacy repositories are read-only reference material.

```text
WRITABLE / AUTHORITATIVE
        quant-platform

READ-ONLY REFERENCE
        ml_core
        legacy documentation
        historical experiments
```

No backward compatibility requirement is assumed unless a future ADR explicitly introduces one.

---

## 2. System-level architecture

```text
                         MARKET DATA SOURCES
                 historical / replay / live feeds
                                │
                                ▼
                           DATA PLANE
              schemas / catalog / partitions / lineage
                                │
                                ▼
                           DataGateway
             ┌──────────────────┴──────────────────┐
             ▼                                     ▼
   Representations / Candles             FeatureDefinition /
             │                          Feature Engine
             └──────────────┐           (canonical data and/or
                            └─────────── representations)
                                              │
                                              ▼
                                      Derived Features
                                      - primitive/base
                                      - higher-level where defined
                                              │
                                              ▼
                                       Research Engine
                    hypotheses / recipes / events
                                │
                                ▼
                          Outcome Engine
                                │
                    ┌───────────┴───────────┐
                    ▼                       ▼
               Event Studies           Label Engine
                    │                       │
                    │                 Supervised ML
                    │                       │
                    └───────────┬───────────┘
                                ▼
                           Policy Layer
                   ┌────────────┼────────────┐
                   ▼            ▼            ▼
                 Rules       ML Policy   Strategic RL
                   └────────────┼────────────┘
                                ▼
                         DecisionIntent
                                │
                                ▼
                     Strategy / Risk Layer
                                │
                                ▼
                         Execution Engine
                    ┌───────────┴───────────┐
                    ▼                       ▼
             Rule/ML Execution         Execution RL
                    └───────────┬───────────┘
                                ▼
                      Orders / Fills / Ledger
                                │
                    ┌───────────┼───────────┐
                    ▼           ▼           ▼
                Historical     Paper       Live
                  Replay       Shadow      Market
                    └───────────┼───────────┘
                                ▼
                        Evaluation Engine
                                │
                                ▼
                        Experiment System
                                │
                                ▼
                               API
                       ┌────────┼────────┐
                       ▼        ▼        ▼
                    App UI     TUI      CLI
```

---

## 3. Layer ownership

### 3.1 Data Plane

Owns:

- canonical market-data contracts;
- dataset and partition identity;
- physical storage references;
- lineage;
- data quality metadata;
- historical/live source registration.

The producer-side Data Plane is elaborated by
[MARKET_DATA_INGEST.md](MARKET_DATA_INGEST.md). Market Data Ingest covers
source/venue adapters, historical and backfill acquisition, live collection,
canonicalization, quality/reconciliation, and partition publication. Storage
Lifecycle covers physical placement, protection, capacity, and health without
creating a second logical data layer or identity system.

It does not own research hypotheses, trading rules or UI semantics.

### 3.2 DataGateway

Owns the canonical read boundary into market data.

Responsibilities:

- dataset discovery;
- partition resolution;
- time slicing;
- column projection;
- replay-source access;
- provenance returned with reads;
- consistent access semantics for local historical, remote and live sources.

Clients above the DataGateway must not open canonical storage directly.

### 3.3 Representation layer

Owns reproducible derived representations such as candles.

Responsibilities include:

- CandleDefinition;
- interval alignment;
- partial/closed semantics;
- late-event handling;
- reproducible aggregation;
- representation provenance.

### 3.4 Feature Engine

Owns derived market observables.

Provider families:

- Trades;
- L1;
- Footprint;
- L2;
- L3;
- Technical;
- Context;
- Custom.

The provider family describes input semantics, not a separate architecture.

The DataGateway is the canonical read boundary for market data. Representations
and candles are first-class reproducible derived representations, but are not
mandatory intermediaries for feature computation. A FeatureDefinition and the
Feature Engine may consume canonical market data directly, representations,
or both, according to explicit input requirements and grain. Feature Engine
outputs include primitive/base derived features and higher-level features where
defined; primitive features are not the universal name for every output.

The Feature Engine owns:

- FeatureDefinition;
- input requirements;
- grain;
- temporal availability;
- deterministic computation;
- cache/materialization policy;
- feature identity;
- lineage.

### 3.5 Research Engine

Owns formulation and inspection of market hypotheses.

Responsibilities:

- HypothesisSpec / Recipe;
- EventSpec;
- event detection;
- event enumeration;
- explainability;
- regime filters;
- parameter sweeps;
- research-level validation.

It consumes features. It does not own feature computation.

### 3.6 Outcome Engine

Owns path-dependent descriptions of what happened after an observation/event.

Examples:

- forward returns;
- MFE/MAE;
- barrier outcomes;
- time-to-event;
- cost-aware path outcomes.

Outcome computation is separate from label construction and strategy simulation.

### 3.7 Label Engine

Owns explicit transformation from outcomes or policy simulations into learning targets.

It must preserve label provenance and timing.

### 3.8 Validation Engine

Owns shared temporal and statistical primitives.

Shared primitives may include:

- warmup;
- rolling/expanding windows;
- purge;
- embargo;
- validation/test/lockbox boundaries;
- availability-time enforcement.

Event-study validation, supervised validation and RL evaluation may use different procedures while sharing the same temporal primitives.

### 3.9 Policy Layer

Owns decision generation independently from execution.

Supported policy families:

- deterministic/rule policies;
- supervised-ML-assisted policies;
- strategic RL policies.

All converge to DecisionIntent.

### 3.10 Strategy / Risk Layer

Owns stateful trading constraints surrounding DecisionIntent.

Responsibilities may include:

- position policy;
- sizing;
- portfolio risk;
- session policy;
- cooldown;
- risk budget;
- exposure constraints.

### 3.11 Execution Engine

Owns transformation of DecisionIntent into executable order behavior.

Responsibilities:

- order intent;
- market/limit semantics;
- fees;
- slippage;
- latency;
- fills;
- cancel/replace;
- execution state.

Execution RL, if used, is an implementation of execution policy inside this boundary, not a separate trading domain.

### 3.12 Market Replay / Live runtime

Owns the source of market events and clock progression.

Historical replay and live runtime must feed compatible domain events into strategy/execution semantics.

### 3.13 Portfolio / Ledger

Owns:

- positions;
- cash/equity;
- realized/unrealized PnL;
- exposure;
- fills;
- transaction history;
- reconciliation state.

### 3.14 Evaluation Engine

Owns metric computation, separated by purpose:

- research metrics;
- strategy/economic metrics;
- learning/generalization metrics.

### 3.15 Experiment System

Owns reproducibility and comparison.

Core concepts:

- Study;
- Trial;
- Run;
- Artifact.

It persists identities, provenance, configuration, metrics and artifact references.

### 3.16 API

The API is the canonical product boundary for interactive clients and automation.

App UI, TUI and CLI consume the API rather than invoking domain engines independently.
The semantic boundary, identity separation, error policy and future job path are
defined by [Consumer API Boundary Contract v1](../contracts/CONSUMER_API.md).

### 3.17 App UI

Owns visual interaction, especially:

- candles;
- footprint;
- market microstructure overlays;
- event annotations;
- entries/exits;
- labels/predictions;
- live/paper/shadow observation.

No quantitative domain logic.

### 3.18 TUI

Owns high-speed keyboard-driven research workflow:

- dataset selection;
- feature/hypothesis configuration;
- event studies;
- validation;
- backtests;
- runs;
- comparisons.

No quantitative domain logic.

### 3.19 CLI

Owns scripting, automation and reproducible batch calls through the API.

No duplicate domain engine.

---

## 4. Core data flow

### 4.1 Research flow

```text
Dataset
  ↓
Features
  ↓
Hypothesis/EventSpec
  ↓
Outcomes
  ↓
Research Statistics
  ↓
Temporal/OOS Validation
```

Purpose:

> establish whether a phenomenon exists and is stable.

### 4.2 Strategy flow

```text
Features / Research evidence / Model predictions
  ↓
Policy
  ↓
DecisionIntent
  ↓
Strategy + Risk
  ↓
Execution
  ↓
Portfolio / Ledger
  ↓
Strategy Evaluation
```

Purpose:

> establish whether a monetizable stateful trading policy exists.

### 4.3 Supervised flow

```text
Features
  +
Outcome/Policy-derived Labels
  ↓
Train / Validate / Lockbox
  ↓
Predictions
  ↓
Policy
```

### 4.4 Strategic RL flow

```text
Features
+ supervised predictions (optional)
+ portfolio state
+ risk state
  ↓
Strategic RL Policy
  ↓
DecisionIntent
```

### 4.5 Execution RL flow

```text
DecisionIntent
+ L1/L2/L3 execution state
+ inventory/order state
  ↓
Execution RL Policy
  ↓
order actions
```

---

## 5. Temporal architecture

Every domain object must expose the timestamps necessary to prove causality.

The exact fields vary by entity, but the architecture must support:

```text
event_time
observation_time
feature_available_time
signal_available_time
decision_time
order_submit_time
fill_time
```

Minimum causal invariant:

```text
information_used_by_decision <= decision_time
order_submit_time >= decision_time
```

For closed candle strategies:

```text
feature_available_time >= candle_close_time
```

unless the feature explicitly operates on a partial/intrabar representation.

---

## 6. Feature granularity

Feature grain is explicit, never inferred only from a column name.

Expected grains include:

- trade;
- quote;
- book_event;
- price_level;
- candle;
- session;
- instrument;
- portfolio/context.

Cross-grain joins require explicit temporal semantics.

---

## 7. Feature lifecycle

A feature computation may be:

### EPHEMERAL

Computed for one operation and not retained.

### CACHEABLE

Safe to reuse when its complete fingerprint matches.

### MATERIALIZED

Persisted as an official derived dataset with lineage and partition metadata.

Materialization is not required for every experiment.

---

## 8. Server-side feature computation

Large feature computation should be data-local.

Target flow:

```text
canonical server data
       ↓
Feature Job
       ↓
Feature Engine
       ↓
materialized feature partitions
       ↓
catalog / lineage registration
```

Examples of early server-side primitive trade features:

- aggressive buy/sell volume;
- delta;
- imbalance;
- volume;
- trade count;
- VWAP;
- intensity;
- candle descriptors.

---

## 9. Job runtime

Long-running operations must not depend on a single long HTTP request.

Canonical asynchronous pattern:

```text
POST operation
  ↓
job_id
  ↓
status/progress
  ↓
result/artifacts
```

Likely asynchronous workloads include:

- feature materialization;
- large event studies;
- parameter sweeps;
- large backtests;
- training;
- historical imports.

The job runtime must define cancellation, failure and recovery semantics.

---

## 10. Identity and provenance

The architecture must converge on a unified identity pattern.

A reproducible run may depend on:

- dataset/partition identities;
- feature definitions;
- recipe/hypothesis;
- outcome/label definitions;
- validation configuration;
- policy/strategy configuration;
- execution configuration;
- implementation/code identity;
- runtime/environment identity where relevant.

Different artifact types may have different payloads, but must follow one coherent provenance model.

---

## 11. Historical vs live

Historical replay and live trading must differ primarily in event source and operational constraints, not strategy semantics.

```text
HistoricalSource ─┐
PaperSource ──────┼──► Domain Market Events
LiveSource ───────┘
                           ↓
                     same policy layer
                           ↓
                    same execution boundary
```

Live adds:

- exchange connectivity;
- incremental features;
- real-time partial candles;
- reconciliation;
- risk kill switches;
- operational monitoring;
- recovery.

---

## 12. Legacy adoption rule

Legacy code is admissible only through capability-level adoption.

Procedure:

1. identify a target capability;
2. define or review the target semantic contract;
3. inspect legacy implementation and tests;
4. compare semantics;
5. extract only the useful implementation;
6. remove legacy architectural dependencies;
7. port/adapt tests;
8. add semantic/temporal/integration tests;
9. freeze as canonical.

The following are explicitly not target architecture:

- oversized CoreApp-style service locator;
- multiple training runtimes for the same task;
- business logic in UI clients;
- multiple uncoordinated identity systems;
- direct storage bypass from upper layers;
- generic DAG introduced without demonstrated need.

---

## 13. Initial implementation direction

The first canonical build path should create a vertical foundation shared by all later capabilities:

```text
Governance / contracts
       ↓
DataGateway
       ↓
Candle contract
       ↓
FeatureDefinition / Feature Engine
       ↓
server-side primitive trade features
       ↓
Research + Outcome Engine
       ↓
Temporal Validation
       ↓
StrategySpec + DecisionIntent
       ↓
Execution / Replay
       ↓
Experiment System
       ↓
API
       ↓
App / TUI / CLI
       ↓
Supervised / RL integration
       ↓
Paper / Shadow / Live
```

This order is not a statement that UI or ML are low-value.  
It reflects dependency direction: they should consume stable lower-layer semantics rather than define them.
