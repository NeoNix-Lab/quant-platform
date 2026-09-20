# Scope: Wave 3 DG-E/F07 semantic freeze and implementation frontier, 2026-09-28 to 2026-10-02

## Objective

Record the Wave 3 state after F04 completion and freeze the bounded DG-E/F07
`labels/censoring/lockbox v1` semantics under governance-only issue #95.

This scope authorizes **governance/decision mutation only** for the F07 semantic
freeze and its state reconciliation. It does not authorize F07 runtime code or
tests. A separate bounded implementation issue/scope is required before F07
runtime mutation.

## Baseline

Wave 2 was promoted to `main` via PR #93 at `14fb4b8`.

Wave 3 then integrated F04 through issue #92 / PR #94:

```text
14fb4b8e73fa16321c7f28268e42516c4f0f5053  Wave-3 baseline from main
a06fe615639f600eb7cb77e2b254fdcc39158ae5  CI issue-close support
fd9c8eea1987b2a75d510b0528dcc82001bcfffe  Merge PR #94 (F04)
1c6b41e61ffed85c519ad6f39dd999507e13c4b7  post-F04 governance reconciliation
```

F04 is `COMPLETE`; V5 (`Feature -> Research`) is `COMPLETE`.

## Active decision-gate scope: DG-E / F07

Issue #95 is the bounded governance-only authority for the F07 branch.

Accepted authority:

- F03 owns Outcome horizon/state/censoring/value/causal-availability semantics;
- ADR-0031/F06 owns decision-time availability, warm-up/support,
  dependency-overlap purge, embargo and deterministic candidate
  classification;
- ADR-0008 requires Outcomes to precede Labels;
- F08 DSR/PBO is an independent DG-E branch and remains outside this scope.

ADR-0036 freezes F07 v1 as follows:

- v1 labels are outcome-derived only; policy-derived labels are deferred until
  Strategy/Execution authority exists;
- only `COMPLETE` F03 Outcomes may produce a label value;
  `CENSORED_END_OF_DATA` and `INSUFFICIENT_COVERAGE` remain explicit no-value
  states and are never coerced into classes;
- exact continuous identity and exact ordered-threshold categorical mapping
  are the only v1 transformation families; F03 exact-rational values are
  preserved without float/Decimal rounding drift;
- label causal availability equals the source Outcome causal availability;
- target support includes the F03 boundary observation at `horizon_end`; its
  F06 half-open projection therefore preserves that endpoint (currently
  `[horizon_start, horizon_end + 1ns)`);
- a training target is an F06 `FOLD_COMPLETION` dependency and must be complete,
  available by the fold-completion cutoff and non-overlapping with held-out
  evidence;
- test/lockbox future targets are evaluation outputs, not decision-time
  dependencies of the same sample;
- lockbox v1 is one terminal half-open holdout interval; development samples
  and their dependency/target support may not enter it;
- unrevealed lockbox evidence cannot influence development choices;
- reveal is explicit and irreversible for the claim of an untouched lockbox;
- F07 freezes semantic isolation only, not physical ACL/vault/security
  infrastructure;
- Validation must not import Research runtime types; a future implementation
  uses a narrow Validation-owned projection of F03 evidence without
  reinterpretation.

## Resulting state

```text
F04  Event study/sweeps                 RESOLVED / COMPLETE
F05  Walk-forward schedule              RESOLVED / COMPLETE
F06  Availability/purge/embargo         FROZEN   / COMPLETE   ADR-0031
F07  Labels/censoring/lockbox            FROZEN   / MISSING    ADR-0036
F08  DSR/PBO                             OPEN_BLOCKING / MISSING
```

Planning metrics become:

```text
TOTAL_ATOMS                     = 87
CLASSIFIED_ATOMS                = 87
UNCLASSIFIED_GAPS               = 0
SEMANTIC_FROZEN_OR_RESOLVED     = 63 / 87 = 72.4%
OPEN_BLOCKING                   = 16
OPEN_DEFERABLE                  = 8
ROADMAP_DEFINED                 = 63 + 16 + 8 = 87
ROADMAP_PLANNING_COMPLETENESS   = 87 / 87 = 100%
IMPLEMENTATION_COMPLETE         = 43 / 87 = 49.4%
```

No implementation count changes in this scope.

## Execution frontier after this freeze

The next implementation-ready atom on the Validation -> Strategy/ML path is:

```text
F07  Labels/censoring/lockbox — Decision FROZEN, Impl MISSING
```

Its declared dependencies `F03` and `F06` are complete. F07 implementation
still requires a separate bounded code-and-tests issue/scope.

F08 remains `OPEN_BLOCKING` in its independent DG-E robust-comparison branch.
It is not activated merely because the F07 sibling branch is frozen.

G01 and I04 remain downstream of **implemented** F07; this semantic freeze does
not claim them unlocked for implementation yet.

## Excluded

- any mutation under `src/` or `tests/`;
- F07 runtime implementation;
- F08 DSR/PBO decisions or implementation;
- policy-derived labels requiring Strategy/Execution semantics;
- new F03 Outcome/triple-barrier semantics;
- Strategy/Execution/ML runtime work;
- lockbox ACL/vault/security infrastructure;
- unrelated governance cleanup or other decision-gate branches;
- mutation of `main`.

## Acceptance

DONE means:

1. ADR-0036 is accepted and indexed as the F07 semantic authority.
2. F03 and ADR-0031/F06 remain unchanged and credited.
3. F07 is `FROZEN / MISSING` consistently across DAG, MAP, ROADMAP,
   OPEN_DECISIONS and this scope.
4. DG-E validation/label branch is recorded resolved for F07 while F08 remains
   independently `OPEN_BLOCKING`.
5. Planning metrics are `63/87` frozen-or-resolved, `16` open blocking, `8`
   deferable and `43/87` implementation complete.
6. V6 is no longer semantically blocked by DG-E/F07; it is pending F07
   implementation.
7. The execution frontier rolls to F07 implementation without authorizing it.
8. No runtime code/tests or unrelated governance are changed.
