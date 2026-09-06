# Legacy Adoption Ledger

**Status:** ACCEPTED — Legacy Capability Harvest Audit v1 COMPLETE

Legacy repositories are reference/evidence libraries, never runtime dependencies.

## Audit authority

```text
canonical repository: NeoNix-Lab/quant-platform
canonical baseline:   7f9b8dc9c5671908c5580c525705c42db7263dc7
legacy repository:    NeoNix-Lab/ml_core
legacy baseline:      1adf6ba79bcb766c93c6e487017561565ab8c131
mutation during audit: NONE
tests during audit:    NOT EXECUTED
```

The audit read both repositories from the exact Git objects above. Legacy working-tree and current-HEAD state were not used as evidence.

Decision vocabulary:

- `ADOPT` — semantics and implementation substantially fit the canonical target; extract the minimum clean core.
- `ADAPT` — useful implementation or concepts, but the canonical contract/ownership differs.
- `REVIEW` — an exact unresolved proposition prevents a final classification.
- `REJECT` — not part of the target architecture or not worth harvesting.
- `SUPERSEDED` — useful historically, already replaced sufficiently by canonical implementation.

Priority vocabulary applies only to worthwhile harvest candidates:

- `P1` — strong immediate leverage.
- `P2` — valuable after prerequisite capability.
- `P3` — defer.

## Audit result

```text
capabilities assessed = 31
ADOPT                = 3
ADAPT                = 20
REVIEW               = 1
REJECT               = 6
SUPERSEDED           = 1

P1 = H01, H04, H09, H17
P2 = H02, H03, H05, H06, H07, H08, H10, H11, H12, H15, H16, H18, H19, H20
P3 = H13, H21, H22, H23, H24
```

## Final capability ledger

| ID | Capability | Decision | Priority | Canonical target | Legacy evidence | Final disposition |
|---|---|---|---|---|---|---|
| H01 | Diagonal + stacked imbalance core | ADOPT | P1 | Feature Engine / Footprint | `src/core/orderflow_microstructure.py` — `_ratio`, `compute_diagonal_imbalance`, `compute_stacked_imbalance` | Extract only the pure quantitative core; keep bar-flow, zone reduction, registry and orchestration out. |
| H02 | Flow aggregates, CVD, POC, divergence | ADAPT | P2 | Feature Engine | `src/core/orderflow_microstructure.py` — `compute_bar_flow`, `compute_delta_divergence` | Preserve formulas; make grain, reset/window and availability explicit. |
| H03 | Trend + stacked-zone reabsorption | ADAPT | P2 | Features / Research composition | `src/core/orderflow_primitives.py` — `trend_state`, `stacked_imbalance_zone`, `body_reabsorption` | Preserve explicit formulas; do not preserve zone merging across disjoint runs. |
| H04 | Custom feature declaration/execution | ADAPT | P1 | Feature Engine | `src/core/custom_feature_registry.py` — `CustomFeatureSpec`, `run_custom_feature` | Keep validation ideas; adapt to canonical FeatureDefinition, grain, lineage and provenance. |
| H05 | Built-in technical features | ADAPT | P2 | Feature Engine / Technical | `src/core/core_app.py::create_dataset_with_derived_features` | Extract selected pure formulas only; exclude CoreApp, loaders and target generation. |
| H06 | Event detection + explanation | ADAPT | P2 | Research | `src/core/event_study.py` — `detect_events`, `enumerate_events` | Keep composition/explain; global percentile selection is retrospective and not a causal trigger. |
| H07 | Multi-horizon forward outcomes | ADAPT | P2 | Outcome / Evaluation | `src/core/event_study.py` — `_forward_returns`, `_summarize_horizon` | Preserve explicit delay/horizon semantics; add canonical event-level outcome/incompleteness. |
| H08 | Close-based triple barrier | ADAPT | P2 | Outcome → Label | `src/core/dataset_adaptation.py::generate_triple_barrier_labels` | Preserve first-touch logic; separate censored tail from genuine time barrier. |
| H09 | Explicit selection + leakage guard | ADAPT | P1 | Validation / ML | `src/core/feature_guard.py::validate_feature_selection` | Preserve fail-closed selection checks; add semantic role and temporal-availability checks. |
| H10 | Expanding positional schedule | ADOPT | P2 | Validation | `src/core/supervised.py::build_walk_forward_folds`, `WalkForwardFold` | Adopt only the deterministic interval generator; label-overlap/purge semantics remain caller responsibilities. |
| H11 | Train-fit fold normalization | ADOPT | P2 | Supervised ML | `src/core/supervised.py::FoldNormalizer` | Adopt fit/transform/statistics core; require explicit numeric-domain validation. |
| H12 | Sample uniqueness | ADAPT | P2 | Validation / ML | `src/core/supervised.py::compute_sample_uniqueness_weights` | Preserve concurrency formula; compute only over temporally admissible fold universe. |
| H13 | Supervised wrappers + centroid baseline | ADAPT | P3 | Supervised ML | `src/core/supervised.py` — `train_model`, `predict_model`, `align_probability_columns` | Keep useful encoding/alignment; make model support, weighting and artifacts explicit. |
| H14 | DSR / PBO estimators | REVIEW | — | Evaluation | `src/core/supervised.py` — `deflated_sharpe_ratio`, `probability_of_backtest_overfitting` | Exact estimator/input semantics unresolved; see REVIEW residue below. |
| H15 | Trial accounting | ADAPT | P2 | Experiment System | `src/core/supervised.py` — `TrialRecord`, `TrialLedger` | Keep attempt accounting; converge on canonical Study/Trial/Run and comparable metrics. |
| H16 | Research workflow + snapshots | ADAPT | P2 | Experiment System | `src/core/research_pipelines.py::ResearchPipelinesService` | Preserve configuration/state/result linkage; eliminate duplicate run identity/persistence. |
| H17 | Definition/artifact identity | ADAPT | P1 | Features / Research provenance | `src/core/identity.py` — `feature_definition_id`, `artifact_id` | Preserve definition-vs-content distinction; replace legacy fingerprints with canonical identities and complete inputs. |
| H18 | Versioned recipe library | ADAPT | P2 | Research + Experiment System | `src/core/recipe_registry.py` — `RecipeRegistry`, `diff_specs` | Preserve revision/diff intent; replace legacy storage and identity conflation. |
| H19 | Deterministic replay/backtest | ADAPT | P2 | Strategy / Execution / Portfolio | `src/core/deterministic_backtest.py::run_deterministic_backtest` | Harvest selectively only; legacy loop accounts position before fill and mishandles partial exits. |
| H20 | Synthetic fills + execution costs | ADAPT | P2 | Execution | `src/core/execution_simulator.py::ExecutionSimulator` | Preserve deterministic arithmetic; keep scheduling/order lifecycle in canonical Execution. |
| H21 | RL transition + economic reward | ADAPT | P3 | Strategic RL + Portfolio/Execution | `src/core/not_agnostic_enviroment.py::step` | Preserve verified economic ordering/reconciliation; do not make environment the canonical ledger or expose unrestricted callbacks. |
| H22 | FSM transition policy | ADAPT | P3 | Strategy / Policy → DecisionIntent | `src/core/fsm_execution.py::FSMExecutionEngine` | Extract deterministic policy decision only; no direct balance/order mutation. |
| H23 | ResearchGateway client surface | ADAPT | P3 | Canonical API/client | `src/tui/facade.py` — `ResearchGateway`, `LocalResearchGateway` | Preserve capability-oriented client vocabulary; remove in-process CoreApp/DataFrame/domain execution. |
| H24 | TUI / App workflow | ADAPT | P3 | TUI / App UI | `src/tui/screens.py`, `src/static/app.js` | Preserve workflow/interaction ideas; UI consumes canonical results and owns no quantitative semantics. |
| H25 | Web implementation with embedded quantitative semantics | REJECT | — | App UI | `src/static/app.js` | Do not harvest implementation; H24 retains workflow value. |
| H26 | CoreApp / service locator | REJECT | — | — | `src/core/core_app.py::CoreApp.__init__` | Do not reconstruct this ownership topology. |
| H27 | Independent Run persistence systems | REJECT | — | Experiment System | `core_app.py::_save_runs_history`, `research_pipelines.py::record_run`, `supervised.py::TrialLedger` | Do not port competing stores/identities; useful requirements are covered by H15/H16. |
| H28 | Duplicate EnvFlex / TrainingRunner runtimes | REJECT | — | — | `src/core/environment.py::EnvFlex.step`, `src/runner.py::TrainingRunner` | No parallel runtime; useful invariants are isolated in H21/H22. |
| H29 | TUI artifact cache as persistence | REJECT | — | Artifact persistence | `src/tui/facade.py::_artifact_cache` | Session memory is not canonical reproducible persistence. |
| H30 | Direct CSV / Parquet / SQLite canonical access | REJECT | — | Data Access | `supervised.py::load_supervised_dataframe`, `market_data_store.py::load_dataframe` | Canonical upper layers must use Data Access; do not harvest loaders. |
| H31 | Finite historical `trade-v1` read path | SUPERSEDED | — | Data Access | canonical `src/quant_platform/access/gateway.py::scan/read` | Canonical catalog-backed DataGateway already provides the supported capability; no harvest. |

## Selected first harvest slice — H01

**H01 — pure diagonal / stacked imbalance core** is the selected first implementation slice.

ADOPT applies only to:

```text
_ratio
compute_diagonal_imbalance
compute_stacked_imbalance
```

It does **not** adopt:

```text
compute_bar_flow
stacked_imbalance_zone
legacy engine / registry / orchestration
legacy package topology
```

Credited legacy evidence includes the existing numeric/edge-case tests in `tests/be/test_be_snapshot_v056.py`, specifically the properties covering:

- diagonal rather than horizontal comparison;
- edge-of-range handling;
- zero/absent-volume behavior;
- stacked-run interruption on price gaps;
- no stacked run spanning two bars;
- duplicate-level refusal.

These properties are CREDIT, not a request to recreate an equivalent test campaign merely because the code is harvested.

The remaining production proposition is deliberately **not** claimed as proven by the legacy tests. A canonical implementation still needs an authorized connection between the pure kernel and:

```text
canonical input / grain
validated tick grid
temporal availability
FeatureDefinition provenance
```

H01 selection therefore does not imply that a complete Feature/Footprint runtime already exists.

## REVIEW residue — H14 DSR/PBO

H14 is the only unresolved audit classification and is non-blocking for H01.

Exact unresolved proposition:

```text
Which precisely defined estimators are canonical DSR and PBO,
using which return series,
which trial population,
which comparable fold semantics,
and which reference numeric vectors?
```

The legacy functions are not accepted as canonical estimators merely from their names. `deflated_sharpe_ratio` does not establish full trial-population semantics, `probability_of_backtest_overfitting` uses a simplified selection rule and bounded combinations, and the legacy call path can label net-PnL percentages as Sharpe.

Minimum future evidence required before reclassification:

- accepted Evaluation semantics for both estimators;
- exact input mapping from canonical Trial/Run data;
- reference numeric vectors.

No new bounded context is required by this residue.

## Known legacy defects — do not port unchanged

Confirmed audit risks include:

- deterministic backtest state/equity can depend on a future execution price before the fill;
- partial exits can be treated as full position closure;
- event-study percentile thresholds can be retrospective over the full frame;
- stacked-zone reduction can merge disjoint runs;
- triple-barrier tail truncation can conflate censoring with time-barrier outcomes;
- nominal leakage guards can allow future-resolution fields such as `touch_idx`;
- sample-uniqueness weights can use future-spanning information across folds;
- legacy `oos_sharpe` inputs/meaning are inconsistent with a true Sharpe estimator;
- `EnvFlex` has reward-before-execution ordering;
- dynamic reward compilation uses unsandboxed `exec()`;
- multiple incompatible experiment persistence systems exist;
- direct CSV/Parquet/SQLite pathways are not canonical access;
- `CoreApp` and duplicate RL environment/trainer architectures are not canonical.

## Adoption rule

For future harvest work:

1. start from the final classification above;
2. use the exact legacy commit as evidence authority;
3. credit existing evidence when it proves the required proposition;
4. port only the minimum capability core needed by the canonical target;
5. never make the legacy repository a runtime dependency;
6. mark a legacy source `SUPERSEDED` only after its canonical replacement is accepted.
