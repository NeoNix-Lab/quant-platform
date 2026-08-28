# DEFINITION_OF_DONE.md

**Status:** DRAFT v0.1

A capability is not `READY` because a module or endpoint exists.

---

## 1. Capability state meanings

### MISSING
No canonical implementation exists.

### EXPERIMENTAL
Useful prototype exists, but semantics/API/persistence may still change.

### PARTIAL
Canonical direction exists but required functionality or guarantees are incomplete.

### READY
Meets the applicable Definition of Done below.

### FROZEN
READY plus semantic contract intentionally stabilized; breaking changes require explicit versioning/ADR.

### LEGACY
Exists only as historical/reference implementation and is not canonical.

---

## 2. General Definition of Done

A capability may be marked `READY` only when applicable items are complete.

### Contract

- semantic owner is defined;
- inputs/outputs are explicit;
- failure behavior is explicit;
- invariants are documented;
- temporal semantics are documented.

### Implementation

- one canonical implementation exists;
- no known parallel canonical implementation exists;
- architecture boundaries are respected;
- hidden runtime defaults do not alter semantics.

### Tests

- the repository-owned local gate is `python tools/run_tests.py`;
- the local gate discovers all `tests/test_*.py` script-style tests;
- unit tests;
- semantic contract tests;
- temporal/leakage tests where applicable;
- deterministic regression/reference test where applicable;
- integration test against canonical adjacent layers;
- negative/failure-path tests.

Database and real-data integration tests are separate environment-dependent
validation. They must be reported as not run when their PostgreSQL or source
database prerequisites are unavailable; they are not silently represented as
local Python-suite coverage.

### Provenance

- input identity is captured;
- parameter identity is captured;
- code/implementation identity is captured;
- persisted output identity is reproducible.

### Operations

- logs/errors are actionable;
- long-running work has job status/progress where applicable;
- cancellation/retry/recovery semantics are defined where applicable.

### API

- canonical capability is reachable through the approved application boundary when user-facing;
- API behavior does not require UI-only logic.

### Documentation

- capability map is updated;
- relevant contract/ADR is referenced;
- example or fixture exists where useful.

---

## 3. Feature-specific Definition of Done

A FeatureDefinition is `READY` only if:

- provider family is explicit;
- input requirements are explicit;
- grain is explicit;
- parameters are complete;
- output dtype/schema is explicit;
- availability semantics are explicit;
- implementation is deterministic;
- implementation identity is reproducible;
- missing-data policy is explicit;
- warmup requirements are explicit;
- no lookahead is possible under documented semantics;
- a reference fixture has expected values;
- partition/boundary behavior is tested;
- lineage is persisted when materialized.

---

## 4. Candle-specific Definition of Done

A candle implementation is `READY` only if:

- interval boundaries are frozen;
- timestamp labeling is frozen;
- alignment/timezone policy is frozen;
- empty-interval behavior is frozen;
- late-event behavior is frozen;
- partial vs closed semantics are explicit;
- OHLC rules are deterministic;
- volume semantics are explicit;
- historical and incremental/live computations agree on closed candles;
- boundary fixtures exist.

---

## 5. Research/Event-specific Definition of Done

- HypothesisSpec/EventSpec is reproducible;
- feature dependencies are explicit;
- event timestamp semantics are explicit;
- deduplication/cooldown semantics are explicit;
- event enumeration is deterministic;
- warmup is explicit;
- incomplete future paths are handled explicitly;
- research statistics distinguish IS/OOS where applicable;
- parameter sweeps record every attempted trial.

---

## 6. Outcome/Label-specific Definition of Done

- horizon/path semantics are explicit;
- censoring/incomplete-path behavior is explicit;
- future information cannot leak into decision inputs;
- policy-derived labels fingerprint StrategySpec/ExecutionSpec;
- label distribution diagnostics exist;
- timing tests exist.

---

## 7. Strategy/Backtest-specific Definition of Done

- StrategySpec is complete and fingerprintable;
- DecisionIntent is learner-agnostic;
- position accounting is deterministic;
- order/fill sequence is deterministic for deterministic models;
- fees/slippage/latency semantics are explicit;
- stops/targets and same-timestamp conflicts have explicit ordering rules;
- stateful behaviors are tested;
- trace explains decisions/orders/fills;
- repeated identical run produces identical identity/result under deterministic configuration.

---

## 8. Supervised ML Definition of Done

- feature set is explicit and leakage-checked;
- label definition is explicit;
- preprocessing is fit only on allowed training data;
- folds/lockbox are reproducible;
- baseline model exists;
- model artifacts are versioned/fingerprinted;
- calibration/generalization metrics are recorded where applicable;
- economic evaluation is separated from pure prediction metrics;
- trial accounting prevents cherry-picking.

---

## 9. RL Definition of Done

- task type is explicit: strategic RL or execution RL;
- state contract is explicit;
- action contract is explicit;
- reward contract is explicit;
- no future leakage exists in state;
- environment transition semantics are deterministic where expected;
- multiple seeds are evaluated;
- OOS/lockbox evaluation exists;
- policy artifact identity is reproducible;
- action distribution and policy stability diagnostics exist.

---

## 10. API Definition of Done

- request/response schema is versioned where appropriate;
- domain errors are mapped explicitly;
- long jobs use Job semantics;
- cancellation is supported where applicable;
- no quantitative business logic exists only in client code;
- API tests cover expected and failure behavior.

---

## 11. Live Definition of Done

Live-related capability is not `READY` without:

- reconciliation;
- restart/recovery behavior;
- idempotency where required;
- kill-switch/risk behavior;
- clock/time-source definition;
- stale-data handling;
- disconnected-feed behavior;
- exchange/API error handling;
- audit trail;
- paper/shadow verification before live promotion.

---

## 12. FROZEN criteria

A capability may be marked `FROZEN` when:

- it is already `READY`;
- its semantic contract has been reviewed;
- representative real-data integration passes;
- backward-incompatible semantic change requires a new version;
- capability map and ADR state are updated.

---

## 12. Repository integrity and promotion

Repository changes are complete for controlled server integration only when:

- the change is in reviewed GitHub history;
- the repository-owned deterministic checks and applicable CI pass;
- local Markdown links and referenced repository paths resolve;
- the exact branch, SHA, tag and dirty/clean state are reportable;
- server promotion uses a clean-tree, explicit fetch and fast-forward-only
  update;
- code synchronization uses Git history, never repository file-copy mirroring;
- bulk canonical data, runtime databases, caches and large artifacts remain out
  of Git.

This does not certify live trading or replace server integration and heavy
certification tiers.
