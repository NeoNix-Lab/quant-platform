# Scope: Weekly frontier preparation, 2026-09-21 to 2026-09-25

## Objective

Record the post-Wave-1 resting state and prepare the execution agenda for the
week of 2026-09-21. This is a governance/planning scope only: it does not
activate implementation of C04, F02, F03, issue #73, or any other atom.

No bounded implementation or decision slice is currently authorized by this
file. Before any mutation on a frontier atom, rewrite `SCOPE.md` for exactly
one bounded slice with its objective, baseline, included/excluded work,
acceptance criteria and verification plan.

## Status

The `implement/wave-1` batch (issues #51-#77: E02, D06, K05, A16, B04, I02,
K03, F06, F01, K06, A10, E04, plus the earlier C05/D03/E05/F05/I01/K04
frontier) concluded 2026-09-19. See
[`docs/product/CAPABILITY_MAP.md`](docs/product/CAPABILITY_MAP.md) and
[`docs/product/CAPABILITY_DAG.md`](docs/product/CAPABILITY_DAG.md) for the
reconciled current state.

The completed Producer-Consumer Conformity cycle remains credited under
ADR-0023:

```text
Contract Freeze Gate              PASSED
Conformity Implementation Gate    PASSED
```

These gates are credited foundation evidence, not reopened work.

Issue #73 (Wave 1 Human Golden E2E closeout) is closed. The physical-storage
host ACL was fixed, a real 1440-candle 1m result was captured through the
production Application/DataGateway/D03 path and frozen into the golden
fixture, and the official operator `verify` run returned `GOLDEN E2E: PASS`
(exit 0). Merged via PR #78; durable evidence and the ingestion/publication
scope ruling (credit rather than re-prove already-validated, archived data)
are in `docs/architecture/WAVE1_GOLDEN_E2E_CLOSEOUT_EVIDENCE.md` (PR #80).

## Current frontier

The current execution frontier (decision-complete, missing atoms whose
declared dependencies are satisfied) is:

```text
C04  tool orchestration convergence through the canonical Application seam
F02  EventSpec/detection
F03  OutcomeSpec/Outcome
```

Frontier membership is not implementation authorization.

## Week plan

Issue #73 closed and PR #78/#79/#80 merged before the week starts, so it opens
directly on the frontier rather than on verification of already-completed
work:

```text
2026-09-21  verify current origin/main reflects PR #78/#79/#80; open at most
            one C04 scope for tool orchestration convergence through the
            Application seam
2026-09-22  continue/close the C04 scope; do not open a second atom in parallel
2026-09-23  open at most one F02 scope for EventSpec/detection, only after
            refreshing F01/E04 evidence
2026-09-24  continue/close the F02 scope
2026-09-25  open at most one F03 scope for OutcomeSpec/Outcome, or reserve the
            day for review/reconciliation if C04/F02 ran long
```

If an earlier item blocks, do not silently roll its authority into the next
item. Record the blocker and select the next independent frontier atom with a
fresh bounded scope.

## Activation rules

Activating any item above requires:

1. verify current `origin/main` and branch ancestry;
2. locate the atom in `docs/product/CAPABILITY_DAG.md`;
3. verify its transitive `Requires` path;
4. activate only unresolved decision branches on that path;
5. credit existing evidence rather than rerunning expensive proofs without a
   concrete invalidating concern;
6. define the exact missing proposition and minimum proof;
7. keep unrelated frontier atoms inactive;
8. preserve governance documents unless the new issue is explicitly
   governance-only / governance-reconciliation work.

## Excluded

- Implementing C04, F02, F03 or issue #73 in this planning scope.
- Claiming ASS-03, API transport, Job runtime, clients or live product mode.
- Reopening the Contract Freeze Gate or Conformity Implementation Gate.
- Mutating contracts, ADRs, schemas, source code, tools, fixtures, DDL or
  runtime/server state.

## Acceptance

DONE means:

1. `SCOPE.md` reflects the post-Wave-1 resting state.
2. The credited conformity gate linkage remains visible.
3. The current frontier is exactly `C04,F02,F03`.
4. The week of 2026-09-21 has an explicit candidate agenda.
5. The agenda does not authorize broad or multi-atom implementation.
6. Future implementation still requires rewriting this file for exactly one
   bounded slice.
