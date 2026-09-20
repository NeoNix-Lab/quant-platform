# Scope: Wave 3 DG-E/F08 semantic freeze and implementation frontier, 2026-09-20

## Objective

Resolve the remaining DG-E decision branch for `F08 — DSR/PBO` under governance-only issue #97 and record the resulting Wave 3 state.

This scope authorizes **governance/decision mutation only** for F08. It does not authorize F07 or F08 runtime code/tests. Issue #96 remains the already-prepared F07 implementation issue, but it is not the active mutation scope while #97 is being resolved.

## Baseline

Authoritative routed baseline at activation:

```text
implement/wave-3 @ 7458aa18cadc64a44982cdff7f310819e87b9f8c
```

Credited Wave 3 state before this decision:

```text
F04  Event studies/sweeps          RESOLVED / COMPLETE
F05  Walk-forward schedule         RESOLVED / COMPLETE
F06  Availability/purge/embargo    FROZEN   / COMPLETE   ADR-0031
F07  Labels/censoring/lockbox      FROZEN   / MISSING    ADR-0036
F08  DSR/PBO                       OPEN_BLOCKING / MISSING
```

F04/F05/F07 semantics are not reopened by this scope.

## Active decision-gate scope: DG-E / F08

Issue #97 is the bounded authority. ADR-0037 freezes the robust-comparison v1 semantics.

### Frozen DSR v1

F08 v1 uses **DSR-L**, the original location-only search-adjusted form. The newer DSR-LS and full-search-distribution variants remain distinct future extensions.

Canonical DSR v1 rules include:

- one complete comparable same-frequency excess-return panel;
- canonical non-annualized Sharpe = sample mean / sample standard deviation (`ddof=1`);
- deterministic raw/Pearson skewness/kurtosis moment formulas;
- deterministic full-panel max-Sharpe selection with canonical identity tie break;
- cross-trial sample Sharpe dispersion;
- explicit caller-supplied `K_eff` evidence; F08 does not estimate trial independence;
- Bailey/López de Prado expected-maximum location benchmark;
- PSR evaluation against that benchmark;
- explicit `IID_V1` sampling assumption;
- pinned DSR vector in ADR-0037.

### Frozen PBO v1

F08 v1 uses full **Combinatorially Symmetric Cross-Validation (CSCV)**:

- common complete `T x N` return panel;
- explicit even `S >= 4`, with `T` divisible by `S`;
- `S` equal contiguous temporal blocks;
- every `C(S, S/2)` symmetric IS/OOS combination;
- canonical Sharpe for every trial in every half;
- best IS trial followed to its OOS rank;
- worst rank `1`, best rank `N`, average rank for exact OOS ties;
- `omega = rank/(N+1)`;
- `lambda = ln(omega/(1-omega))`;
- `PBO = count(lambda < 0) / split_count`;
- any required non-evaluable Sharpe makes the complete PBO non-evaluable; no silent filtering;
- pinned 8x4 / `S=4` vector yields exactly `PBO = 1/6`.

F05 walk-forward semantics remain separate; F08 does not reinterpret walk-forward folds as CSCV partitions.

### Ownership

The future F08 estimator runtime is Validation-owned and must preserve the current owner DAG. Validation consumes opaque trial/population evidence through Validation-owned projections and does not import Research/Experiment/Strategy runtime types.

## Resulting state

```text
F04  Event studies/sweeps          RESOLVED / COMPLETE
F05  Walk-forward schedule         RESOLVED / COMPLETE
F06  Availability/purge/embargo    FROZEN   / COMPLETE   ADR-0031
F07  Labels/censoring/lockbox      FROZEN   / MISSING    ADR-0036
F08  DSR/PBO                       FROZEN   / MISSING    ADR-0037
```

DG-E now has **no remaining open semantic branch**. Missing runtime implementations remain explicit.

Planning metrics become:

```text
TOTAL_ATOMS                     = 87
CLASSIFIED_ATOMS                = 87
UNCLASSIFIED_GAPS               = 0
SEMANTIC_FROZEN_OR_RESOLVED     = 64 / 87 = 73.6%
OPEN_BLOCKING                   = 15
OPEN_DEFERABLE                  = 8
ROADMAP_DEFINED                 = 64 + 15 + 8 = 87
ROADMAP_PLANNING_COMPLETENESS   = 87 / 87 = 100%
IMPLEMENTATION_COMPLETE         = 43 / 87 = 49.4%
```

No implementation count changes in this decision scope.

## Execution frontier after this freeze

```text
F07 implementation   FROZEN / MISSING   issue #96 prepared; not activated by this scope
F08 implementation   FROZEN / MISSING   now eligible for a separate bounded implementation issue
```

Wave 3 is semantically frozen end-to-end for F01-F08, but is **not implementation-complete** until F07 and F08 runtimes are complete.

F08 remains independent from the Strategy/Supervised path: F07 completion can unlock its downstream atoms without waiting for F08 runtime.

## Excluded

- mutation under `src/` or `tests/`;
- F07/F08 runtime implementation;
- DSR-LS/full-search DSR;
- serial-correlation-adjusted DSR;
- effective-trial-count estimation algorithms;
- Strategy/Execution/PnL/cost semantics;
- generic metric/plugin/DSL frameworks;
- Experiment persistence/accounting redesign;
- unrelated decision-gate work;
- mutation of `main`.

## Acceptance

DONE means:

1. ADR-0037 is accepted and indexed as F08 semantic authority.
2. H14 REVIEW residue is resolved to ADAPT against ADR-0037 rather than adopting legacy estimators.
3. F08 is `FROZEN / MISSING` consistently across governance projections.
4. DG-E has no remaining open branch; F07/F08 runtime gaps remain explicit.
5. Planning metrics are `64/87` frozen-or-resolved, `15` open blocking, `8` deferable and `43/87` implementation complete.
6. F04/F05/F07 authority remains unchanged and credited.
7. No runtime code/tests or unrelated governance are changed.
