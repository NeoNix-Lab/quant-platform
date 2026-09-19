# Scope: Wave 2 frontier preparation, 2026-09-21 to 2026-09-25

## Objective

Record the corrected post-Wave-1 resting state and prepare the Wave 2 execution
agenda for the week of 2026-09-21.

This file is a governance/planning scope. It does not broaden or combine the
separately bounded implementation authority carried by issues #83 (E06), #85
(F02), and #86 (F03). Any implementation work must remain inside the exact
runtime/code-and-tests boundaries of the selected issue.

## Status

The `implement/wave-1` batch concluded 2026-09-19 and was merged into `main`
through PR #81. PR #82 then froze `E06 — H01 canonical integration v1` under
ADR-0035.

The routed Wave 2 integration branch is:

```text
implement/wave-2
```

It was created from `main` after PR #82. The governance correction commit
`a1e3cc9c70f1b906711d3de020f347e60d989d73` records that C04/ASS-03 was
already complete via issue #40 / PR #44 on 2026-09-14 and had been
misclassified as `MISSING` during the post-Wave-1 reconciliation.

Issue #84 was therefore redundant and is closed. Its work must not be
re-implemented or re-proved absent a concrete invalidating change.

The completed Producer-Consumer Conformity cycle remains credited under
ADR-0023:

```text
Contract Freeze Gate              PASSED
Conformity Implementation Gate    PASSED
```

Issue #73 (Wave 1 Human Golden E2E closeout) is also closed and credited. The
real 1440-candle 1m D03 result, official `GOLDEN E2E: PASS` verification and
durable evidence remain accepted foundation evidence and are not reopened by
Wave 2.

## Current frontier

The current execution frontier is:

```text
E06  H01 canonical integration runtime — ADR-0035 FROZEN, issue #83
F02  EventSpec / causal event detection — issue #85
F03  OutcomeSpec / Outcome — issue #86
```

C04 is not part of the frontier; it is `COMPLETE` via issue #40 / PR #44.

Frontier membership is not blanket implementation authorization. The selected
issue remains the bounded authority for its own runtime mutation surface.

## Wave 2 execution relationship

E06 and F02 are independently executable from the current routed baseline when
their own activation gates hold. Generic F02 does not depend on E06 runtime;
E06 semantics are frozen under ADR-0035 but its runtime remains missing until
issue #83 is completed.

F03 depends on F02 being present on the routed baseline. Do not implement or
cherry-pick missing prerequisites inside #86.

The Wave 2 governance closeout is issue #87. It remains inactive until the E06,
F02 and F03 implementation PRs are merged into `implement/wave-2`. C04 is
credited pre-existing evidence and is not a Wave 2 implementation gate.

## Week plan

The week is organized around the three real frontier atoms rather than the
stale C04 entry:

```text
2026-09-21  verify current origin/implement/wave-2 and branch ancestry;
            begin at most one bounded frontier issue (#83 E06 or #85 F02)
2026-09-22  continue/review the active bounded issue; avoid duplicate proof
2026-09-23  begin/continue the remaining independent E06 or F02 slice after
            refreshing only its named prerequisites and directly affected evidence
2026-09-24  F03 may start only after F02 is available on the routed baseline;
            otherwise use the day for review/closeout of E06 or F02
2026-09-25  continue F03 or reserve for review/integration; activate #87 only
            after E06, F02 and F03 are all merged into implement/wave-2
```

If an item blocks, do not silently roll its authority into another issue.
Record the exact blocker and continue only with an independent frontier atom
whose own prerequisites remain satisfied.

## Activation rules

For any Wave 2 implementation issue:

1. verify current `origin/implement/wave-2` and its ancestry from `origin/main`;
2. read the issue's named authority and directly affected code/evidence first;
3. verify only the transitive prerequisites material to that issue;
4. credit existing evidence rather than rerunning expensive proofs without a
   concrete invalidating concern;
5. identify the exact missing proposition and minimum sufficient proof;
6. keep unrelated frontier atoms inactive;
7. do not mutate governance from code-and-tests issues;
8. stop only on a real authority contradiction, missing semantic decision or
   authorization boundary.

## Excluded

- Reopening or re-implementing C04 / issue #84.
- Treating E06 runtime as a prerequisite of generic F02.
- Combining E06, F02 and F03 into one implementation scope.
- Activating issue #87 before E06, F02 and F03 are merged into
  `implement/wave-2`.
- Claiming API transport, Job runtime, clients, live product mode or unrelated
  future capabilities.
- Reopening the Contract Freeze Gate, Conformity Implementation Gate or Human
  Golden E2E without concrete invalidating evidence.

## Acceptance

DONE means:

1. `SCOPE.md` reflects the corrected Wave 2 routed baseline and C04 evidence.
2. C04 is credited as `COMPLETE`; issue #84 is treated as redundant/closed.
3. The current frontier is exactly `E06,F02,F03`.
4. E06 and F02 independence, and the F02 -> F03 prerequisite, are explicit.
5. Issue #87 waits only for E06, F02 and F03 Wave 2 merges.
6. Existing conformity and Golden E2E evidence remains credited rather than
   re-proved.
7. `main` is not mutated by this scope reconciliation.
