# Scope: Wave 3 resting state (F04 completion) and DG-E preparation, 2026-09-28 to 2026-10-02

## Objective

Record the resting state following the completion of F04 (EventStudySpec and parameter
sweep runtime v1) on `implement/wave-3`, and prepare the execution agenda for the DG-E
decision gate.

This file is a governance/planning scope. It does not itself authorize any
implementation or decision-gate mutation; a separately bounded `SCOPE.md`/
issue is required before any runtime/code-and-tests work is activated.

## Status: Wave 3 resting state (F04 complete)

Wave 2 was promoted and merged to `main` via PR #93 at commit `14fb4b8`
(`Merge pull request #93 from NeoNix-Lab/implement/wave-2`).

`implement/wave-3` was branched from `main` to serve as the temporary authority and
integration branch for Wave 3.

The F04 implementation (issue #92) is **COMPLETE**, verified by the following exact
merge-commit ancestry on `implement/wave-3` (identical to `origin/implement/wave-3`):

```text
14fb4b8e73fa16321c7f28268e42516c4f0f5053  baseline: Merge PR #93 (implement/wave-2 into main)
a06fe615639f600eb7cb77e2b254fdcc39158ae5  ci: auto-close linked issue when PR is merged into implement/** or main
fd9c8eea1987b2a75d510b0528dcc82001bcfffe  Merge PR #94 (issue #92, F04) onto a06fe61
                                           head agent/issue-92-26 @ a3ea9ff182712774d43afdfe123feeaa3efb4517
```

`implement/wave-3` and `origin/implement/wave-3` both point to
`fd9c8eea1987b2a75d510b0528dcc82001bcfffe`, confirming that PR #94 landed on the routed
integration branch and closed issue #92.

```text
F04  Event study/sweeps v1   COMPLETE   issue #92 / PR #94 / fd9c8ee
```

All decision-complete Research atoms (`F01`, `F02`, `F03`, `F04`) are now `COMPLETE`.
Vertical milestone `V5` (`Feature -> Research`) is `COMPLETE`.

This governance reconciliation records F04 as `COMPLETE` across `CAPABILITY_MAP.md`,
`CAPABILITY_DAG.md`, `ROADMAP.md` and `SCOPE.md`.

The completed Producer-Consumer Conformity cycle remains credited under
ADR-0023:

```text
Contract Freeze Gate              PASSED
Conformity Implementation Gate    PASSED
```

## Execution agenda: DG-E Decision Gate

With F04 complete, the Research / Validation path reaches the **DG-E** decision gate:

```text
DG-E  Validation semantics (F07 labels/censoring/lockbox, F08 DSR/PBO)
```

- `F07` (Labels/censoring/lockbox, `OPEN_BLOCKING`): declared dependencies `F03` and `F06`
  are both `COMPLETE`. It unlocks `G01` (StrategySpec) and `I04` (Supervised input/selection).
  F07 requires resolving:
  - temporal availability rule used across Feature/Research/Validation;
  - purge/embargo/warmup semantics at fold boundaries;
  - label/outcome horizon and censoring semantics;
  - lockbox / hidden-evaluation boundary.
- `F08` (DSR/PBO, `OPEN_BLOCKING`): declared dependencies `F04` and `F05` are both `COMPLETE`.
  It unlocks robust strategy comparison. F08 does not block Strategy or supervised ML paths.

Candidate next step:
- Open a bounded decision-gate scope/issue for DG-E (resolving F07 validation/labeling semantics).

Only one bounded implementation or decision-gate scope may be active at a
time, per the Planning rule in `ROADMAP.md`/`CAPABILITY_DAG.md`.

## Excluded

- Activating DG-E decision gate or F07 implementation directly without an explicit,
  separately bounded issue/scope.
- Mutating code under `src/` or `tests/` in governance-only issues.
- Prematurely freezing second-provider, live, RL, transport or operational semantics.
- Reopening completed milestones (V1-V5) or gates (Contract Freeze Gate, Conformity Gate, Golden E2E).

## Acceptance

DONE means:

1. `SCOPE.md` reflects the Wave 3 resting state with exact merge-commit ancestry
   evidence (`14fb4b8`, `a06fe61`, PR #94 `fd9c8ee` for issue #92 F04).
2. F04 is credited as `COMPLETE` across `CAPABILITY_MAP.md`, `CAPABILITY_DAG.md`,
   `ROADMAP.md` and `SCOPE.md`.
3. Vertical milestone `V5` is recorded as `COMPLETE` (F02, F03, F04).
4. Total atom implementation count is updated to 43 / 87 = 49.4%.
5. The execution frontier is rolled to DG-E (`F07`, `F08`).
6. All tests and markdown link checks pass.
