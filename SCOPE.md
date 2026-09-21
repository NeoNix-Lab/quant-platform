# Scope: Complete Wave 3 — F07 then F08

## Objective

Complete **Wave 3 — Research / Validation** on `implement/wave-3` by implementing the two remaining runtime atoms whose semantics are already frozen:

```text
F07  Labels/censoring/lockbox   FROZEN / MISSING   ADR-0036   issue #96
F08  DSR/PBO                    FROZEN / MISSING   ADR-0037   issue #98
```

Wave 3 completion is the current project scope. This is a macro outcome, not authorization for concurrent mutation.

## Baseline

Authoritative Wave 3 baseline before runtime completion work:

```text
implement/wave-3 @ 94ef9cce3e008b1468a1520c109d3b65f834bb0d
```

This baseline contains the completed DG-E semantic freeze:

- F07 frozen by ADR-0036;
- F08 frozen by ADR-0037;
- DG-E has no remaining open semantic branch;
- F01-F06 are already implementation complete;
- F07 and F08 are the only remaining Wave 3 runtime gaps.

If the branch advances before an agent starts, use the current fast-forward descendant and verify only directly relevant authority/evidence.

## Execution policy

The repository rule remains: **only one bounded implementation or decision-gate mutation slice is active at a time**.

Therefore Wave 3 completes in this order:

```text
1. #96  F07 implementation
        ↓
   merge + targeted governance reconciliation
        ↓
2. #98  F08 implementation
        ↓
   merge + final Wave 3 reconciliation
        ↓
3. Wave 3 COMPLETE
```

Opening/preparing #98 does not make it concurrently active.

## Active bounded slice

**Issue #96 — F07 outcome-derived labels, censoring and lockbox v1** is the active runtime mutation authority.

Its authority and hard boundaries are defined by the issue itself and ADR-0036. In particular:

- F03 and F06 semantics remain credited and unchanged;
- Validation must preserve its package boundary;
- governance documents are not agent-owned implementation scope;
- F08 runtime is excluded while #96 is active.

DONE for this slice means #96 acceptance passes, required verification passes, and F07 is integrated into the routed Wave 3 branch.

## Next bounded slice

**Issue #98 — F08 canonical DSR-L / CSCV-PBO robust comparison v1** is prepared and implementation-ready, but is **not active while #96 is active**.

After F07 integration and the minimum governance reconciliation needed to record `F07 COMPLETE`, activate #98 without reopening ADR-0037.

F08 implementation must remain bounded to the Validation-owned estimator foundation frozen by ADR-0037.

## Credited Wave 3 state

```text
F01  HypothesisSpec                  RESOLVED / COMPLETE
F02  EventSpec/detection             RESOLVED / COMPLETE
F03  OutcomeSpec/Outcome             RESOLVED / COMPLETE
F04  Event studies/sweeps            RESOLVED / COMPLETE
F05  Walk-forward schedule           RESOLVED / COMPLETE
F06  Availability/purge/embargo      FROZEN   / COMPLETE   ADR-0031
F07  Labels/censoring/lockbox        FROZEN   / MISSING    ADR-0036
F08  DSR/PBO                         FROZEN   / MISSING    ADR-0037
```

Do not re-prove completed F01-F06 evidence absent a concrete invalidating change.

The completed Producer-Consumer Conformity cycle remains credited under ADR-0023:

```text
Contract Freeze Gate              PASSED
Conformity Implementation Gate    PASSED
```

## Wave 3 acceptance

Wave 3 is DONE only when all of the following are true:

1. #96 F07 implementation is integrated and its acceptance/authoritative verification pass.
2. #98 F08 implementation is integrated and its acceptance/authoritative verification pass.
3. F07 and F08 are both recorded `FROZEN / COMPLETE` in canonical governance projections.
4. F01-F08 are implementation complete with no unresolved DG-E decision.
5. V6 / Research -> Validation state is reconciled to the strongest proposition actually proven by the completed runtime.
6. No Strategy, Execution, ML, live-data, operations or unrelated atom is pulled into Wave 3 completion.
7. Final governance reconciliation reflects the integrated repository state without reopening accepted ADR semantics.

## Verification proportionality

For each implementation slice:

- use targeted tests during implementation;
- run the repository-required authoritative suite once on the stable candidate;
- credit existing PASS evidence unless a later relevant change/failure/rebase creates a reason to re-run it;
- perform governance reconciliation only after integrated runtime evidence exists.

Do not create extra proof layers merely because Wave 3 is the macro scope.

## Stop / escalation

Stop only if:

- current repository state contradicts ADR-0036 or ADR-0037;
- completing F07/F08 requires changing frozen upstream semantics;
- package ownership must be loosened to satisfy acceptance;
- an implementation exposes a genuine production defect outside the bounded atom that cannot be isolated/remediated minimally;
- completing Wave 3 would require authorization for an atom outside F01-F08.

Do not escalate local reversible implementation choices already inside #96/#98.

## Out of scope

Until Wave 3 is complete, do not activate as part of this scope:

- G01+ Strategy / Replay work;
- I04+ Supervised ML work;
- DG-F, DG-G or DG-H branches;
- live-data/runtime/client work;
- unrelated governance cleanup;
- mutation of `main` except through the separately authorized integration/promotion workflow.
