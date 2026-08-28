# Legacy Adoption Ledger

**Status:** DRAFT v0.1

Legacy repositories are reference libraries, not runtime dependencies.

Decision vocabulary:

- `ADOPT` — semantics and implementation largely fit target; extract cleanly.
- `ADAPT` — useful implementation/concepts, but target contract differs.
- `REVIEW` — insufficient evidence yet.
- `REJECT` — not part of target architecture.
- `SUPERSEDED` — useful historically, replaced by canonical implementation.

---

| Legacy area | Preliminary decision | Target capability | Notes |
|---|---|---|---|
| `deterministic_backtest.py` | REVIEW → likely ADOPT | Historical backtest / replay | strongest legacy canonical candidate |
| `execution_simulator.py` | REVIEW → likely ADAPT/ADOPT | Execution Engine | compare order/fill semantics first |
| `orderflow_microstructure.py` | ADAPT | Feature Engine providers | order-flow becomes provider family, not peer engine |
| historical `orderflow_primitives.py` | REVIEW | Trade/Footprint providers | inspect deleted/history version |
| historical `event_study.py` | REVIEW → likely ADAPT | Research + Outcome Engine | retain event-study semantics, remove TUI/cache coupling |
| custom feature registry | ADAPT | Feature Engine / Custom provider | unify identity and provider contract |
| built-in CoreApp feature helpers | REVIEW selectively | Feature Engine | extract formulas, not CoreApp architecture |
| feature leakage guard | REVIEW → likely ADOPT | Validation / ML | strong reusable invariant |
| supervised expanding walk-forward | REVIEW → likely ADOPT primitives | Validation Engine | preserve domain-specific validation semantics |
| fold normalization | REVIEW → likely ADOPT | Supervised ML | ensure fit-only-on-train invariants |
| supervised model wrappers | REVIEW | Supervised ML | retain only useful model implementations |
| DSR/PBO/trial accounting | REVIEW → likely ADAPT | Evaluation / Experiment System | integrate with unified Study/Trial/Run |
| `ResearchPipelinesService` | ADAPT concepts | Experiment System | avoid carrying duplicate persistence semantics |
| historical ResearchGateway | ADOPT pattern | API client/SDK | thin client pattern is valuable |
| historical TUI screens | ADAPT UX | TUI | preserve workflow ideas, not hidden business logic |
| old web UI | REJECT implementation / REVIEW UX | App UI | UI business logic must not be canonical |
| `CoreApp` | REJECT architecture | — | oversized façade/service locator |
| `CoreApp.runs` / run history persistence | REJECT as separate identity | Experiment System | converge on one Run semantics |
| duplicate `TrainingRunner` runtime | REJECT architecture | RL/Training | evaluate algorithms only |
| `NotAgnosticEnvFlex` | REVIEW | Strategic RL | inspect state/action/reward semantics |
| `EnvFlex` / FSM execution | REVIEW | Strategic/Execution RL | determine useful execution mechanics |
| in-memory TUI artifact cache | REJECT | Artifact cache | cache must be reproducible/persistent |
| historical event-study identity module | ADAPT concepts | Provenance | merge into unified identity pattern |
| direct CSV/Parquet loaders | REJECT canonical path | DataGateway | formulas/tests may remain useful |
| legacy SQLite market-data store | REJECT future data plane | DataGateway | canonical future data plane is server catalog + Parquet |

---

## Adoption procedure

For each candidate:

1. define target capability;
2. identify target contract;
3. locate implementation/tests/docs in legacy;
4. document semantic match/mismatch;
5. select ADOPT/ADAPT/REJECT;
6. port only needed code;
7. remove legacy runtime dependencies;
8. add target semantic tests;
9. update this ledger with commit/target path;
10. mark source as `SUPERSEDED` once canonical replacement is accepted.

## Known defects: do not port unchanged

- Triple-barrier tail truncation can conflate censored tail rows with genuine time-barrier outcomes.
- The leakage guard permits future-resolution fields such as `touch_idx`.
- Sample-uniqueness weights can use future-spanning information across folds.
- `oos_sharpe` can store mean net PnL percentage rather than a true Sharpe ratio.
- `EnvFlex` has reward-before-execution ordering.
- Dynamic reward compilation uses unsandboxed `exec()`.
- Multiple incompatible experiment persistence systems exist.
- Direct CSV/Parquet/SQLite pathways are not canonical access.
- `CoreApp` service-locator architecture and duplicate RL environment/trainer architectures are not canonical.

Preserve useful test patterns: truncation invariance, causal order-flow and
event-study tests, reward/equity reconciliation, and execution cost
double-charge checks.

Durable historical reference: [ml_core commit
1adf6ba79bcb766c93c6e487017561565ab8c131](https://github.com/NeoNix-Lab/ml_core/tree/1adf6ba79bcb766c93c6e487017561565ab8c131).
Recoverable paths include `src/core/event_study.py`,
`src/core/orderflow_primitives.py`, `src/core/identity.py`,
`src/core/recipe_registry.py` and `src/tui/`.
