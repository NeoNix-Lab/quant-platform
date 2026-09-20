# Scope: Post-Wave-2 governance closeout and Wave 3 frontier preparation, 2026-09-28 to 2026-10-02

## Objective

Record the resting state at the close of the Wave 2 week (2026-09-21 to
2026-09-25) and prepare the execution agenda for the following week
(2026-09-28 to 2026-10-02).

This file is a governance/planning scope. It does not itself authorize any
implementation or decision-gate mutation; a separately bounded `SCOPE.md`/
issue is required before any runtime/code-and-tests work is activated.

## Status: Wave 2 resting state (close of 2026-09-25)

The Wave 2 frontier (issues #83 E06, #85 F02, #86 F03) is **COMPLETE** and
merged into `implement/wave-2`, then into `main`:

```text
E06  H01 canonical integration   COMPLETE   issue #83 / PR #88
F02  EventSpec/detection         COMPLETE   issue #85 / PR #89
F03  OutcomeSpec/Outcome         COMPLETE   issue #86 / PR #90
```

This governance issue (#87) performs the reconciliation: `CAPABILITY_MAP.md`,
`CAPABILITY_DAG.md`, `ROADMAP.md` and `OPEN_DECISIONS.md` now record E06, F02
and F03 as `COMPLETE`, and Wave 2 as concluded.

C04/ASS-03 remains credited pre-existing evidence (issue #40/PR #44,
2026-09-14) and was never part of the Wave 2 implementation gate. Issue #73's
Wave 1 Human Golden E2E closeout (1440-candle 1m D03, official
`GOLDEN E2E: PASS`) remains closed and credited; the previously stale
"in progress" notes in `CAPABILITY_MAP.md`/`ROADMAP.md` are corrected by this
reconciliation.

The completed Producer-Consumer Conformity cycle remains credited under
ADR-0023:

```text
Contract Freeze Gate              PASSED
Conformity Implementation Gate    PASSED
```

## Next week execution agenda (2026-09-28 to 2026-10-02)

The next decision-complete, dependency-satisfied atom is:

```text
F04  Event study/sweeps — Requires F02,F03,B03, all COMPLETE; decision RESOLVED
```

Beyond `F04`, the Validation/Strategy path remains blocked by the DG-E
decision gate (`F07` labels/censoring/lockbox, `OPEN_BLOCKING`). F07's
dependencies (`F03`,`F06`) are now both `COMPLETE`, but the gate itself is
not resolved, so it does not join the frontier until a decision-gate scope
resolves it.

Candidate agenda for the week, in order of readiness:

```text
2026-09-28  verify current origin/main ancestry after Wave 2 merge;
            open a bounded SCOPE.md for F04 (Event study/sweeps) if selected
2026-09-29  continue F04, or open a bounded decision-gate scope for DG-E
            (F07 labels/censoring/lockbox) if F04 is not selected this week
2026-09-30  continue the selected bounded scope; avoid duplicate proof
2026-10-01  continue/review the selected scope
2026-10-02  close the selected scope; reserve remainder for review/integration
```

Only one bounded implementation or decision-gate scope may be active at a
time, per the Planning rule in `ROADMAP.md`/`CAPABILITY_DAG.md`.

## Excluded

- Activating `F04` implementation or the DG-E decision gate directly from
  this governance issue (issue #87 is governance-only; a separate bounded
  scope/issue is required).
- Reopening or re-implementing C04 / issue #84, or the Wave 1/Wave 2
  implementation issues.
- Reopening the Contract Freeze Gate, Conformity Implementation Gate or
  Human Golden E2E without concrete invalidating evidence.
- Claiming API transport, Job runtime, clients, live product mode or other
  unrelated future capabilities.

## Acceptance

DONE means:

1. `SCOPE.md` reflects the Wave 2 resting state and the corrected Wave 3
   frontier candidate (`F04`) plus the next blocking decision gate (DG-E).
2. E06, F02 and F03 are credited as `COMPLETE` across governance documents.
3. Wave 2 is recorded as `COMPLETE` in `CAPABILITY_MAP.md`, `CAPABILITY_DAG.md`
   and `ROADMAP.md`.
4. Issue #73's stale "in progress" note is corrected to closed/credited.
5. `main` is not mutated by this scope reconciliation beyond the authorized
   governance files.
