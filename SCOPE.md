# Scope: Post-Wave-1 Implementation Reconciliation

## Objective

Reconcile canonical governance with the implementation state already integrated in `main` after the first ready-slice wave, without changing any frozen semantic contract or prematurely crediting unmerged work.

This reconciliation records the integrated completion of C05, C04/ASS-03, K04, D03, F05 and I01. E05 remains `RESOLVED / MISSING` because PR #50 is merge-ready but not integrated in `main` at this baseline.

The completed Producer–Consumer Conformity cycle remains credited under ADR-0023: **Contract Freeze Gate = PASSED** and **Conformity Implementation Gate = PASSED**. This slice does not reopen either gate.

## Baseline

```text
base main                  = 35109010e19b31bd480dc87a377453ff22ee5474
latest integrated slice    = PR #49 / I01
mutation class             = governance docs only
source/tests/runtime       = NO
contracts/ADRs             = NO
unmerged candidate credit  = NO
```

Integrated implementation evidence credited by this reconciliation:

```text
PR #43  C05  Configuration convergence                 merge fb87a021c7134078b2a39f48ceb5f831b016e8ed
PR #44  C04  Tool orchestration convergence / ASS-03   merge b25e6be0cea1403b233664c7fea90adcd2e33ace
PR #46  K04  Observational capacity                    merge a19662411f6ce125bcf19ce176f6373736b583d5
PR #47  D03  Historical Candle computation             merge 652affcd9be573aca0f4103018ea02eda153b64a
PR #48  F05  Deterministic walk-forward schedule       merge 08fce020836a934a649c29a2fcb1ede74fe72856
PR #49  I01  Study/Trial/Run/Artifact identities       merge 35109010e19b31bd480dc87a377453ff22ee5474
```

The corresponding reviewed candidates and exact-head CI are credited and must not be re-proved absent a concrete invalidating change. F05's integration drift was reconciled before merge; its final candidate included D03 and passed exact-head integrity.

## Accepted state

```text
C01  Application ownership                    COMPLETE
C02  ASS-02 semantic selector resolution      COMPLETE
C03  ASS-02 result/error translation          COMPLETE
C05  Configuration convergence                COMPLETE
C04  Tool orchestration convergence           COMPLETE
ASS-03 executable orchestration convergence   COMPLETE
D03  Historical Candle computation            COMPLETE
F05  Walk-forward schedule                    COMPLETE
I01  Study/Trial/Run/Artifact semantic model  COMPLETE
K04  Capacity observation                     COMPLETE
E05  H01 pure imbalance kernel                RESOLVED / implementation MISSING
```

C04 completion means the finite governed `tools -> runtime/domain/tests` ASS-03 debt has converged through the canonical Application seam. It does not imply API transport, Job runtime, clients, live ingest or any downstream quantitative capability.

## Current execution frontier

Decision-complete, missing atoms whose declared planning dependencies are satisfied at this baseline:

```text
E05  H01 pure imbalance kernel
```

PR #50 is an implementation candidate for E05 and is merge-ready, but frontier/canonical implementation state follows `main`; therefore E05 remains `MISSING` until that PR is integrated.

The next useful orchestration supply comes from bounded decision branches, not from pretending their implementations are already ready. High-value branches include:

```text
DG-A  E02 / D06 / E04 toward canonical H01 integration
DG-E  F06 availability / purge / embargo
DG-G  I02 experiment persistence
DG-H  K05 pressure policy, then K06 / K08
DG-B  A16 / B04 / A10 repair path as required
```

This list is prioritization guidance only. Each `OPEN_BLOCKING` atom still requires its own decision freeze before implementation authorization.

## Scope

Included:

- record C05 as `RESOLVED / COMPLETE`;
- record C04 as `RESOLVED / COMPLETE` and ASS-03 as complete;
- record D03 as `FROZEN / COMPLETE` and V3 as complete;
- record F05 as `RESOLVED / COMPLETE`;
- record I01 as `RESOLVED / COMPLETE`;
- record K04 as `RESOLVED / COMPLETE`;
- reduce every current-frontier representation to E05 only;
- update DG-D from resolved-but-unimplemented to resolved-and-complete historical reference;
- preserve E05 as `RESOLVED / MISSING` until PR #50 is actually merged;
- preserve all unrelated dependency edges, decision states, gate semantics and deferable triggers.

Excluded:

- any source, test, runtime, schema, DDL or fixture mutation;
- any ADR/contract semantic change;
- crediting or merging PR #50;
- resolving E02, D06, E04, F06, I02, K05, K06, K08, A16, B04 or A10;
- API transport, Job runtime, client or live-ingest implementation;
- re-review or re-execution of already-credited implementation proof.

## Preserved planning invariants

```text
TOTAL_ATOMS                     = 87
CLASSIFIED_ATOMS                = 87
UNCLASSIFIED_GAPS               = 0
SEMANTIC_FROZEN_OR_RESOLVED     = 50 / 87 = 57.5%
OPEN_BLOCKING                   = 29
OPEN_DEFERABLE                  = 8
ROADMAP_PLANNING_COMPLETENESS   = 87 / 87 = 100%
REQUIRES_EDGES                  = 157
CYCLES                          = 0
```

No decision-state movement occurs in this reconciliation. The changes above are implementation-state transitions only.

## Acceptance

DONE means all of the following are true:

1. C04, C05, D03, F05, I01 and K04 are `COMPLETE` everywhere canonical governance represents implementation state;
2. ASS-03 is recorded complete without implying API/jobs/clients/live capability;
3. V3 is `COMPLETE` because D03 is integrated;
4. every current-frontier list is exactly `E05` at baseline `35109010...`;
5. E05 remains `RESOLVED / MISSING` because PR #50 is not yet in `main`;
6. DG-D is a resolved/completed historical reference, not a live blocker;
7. planning metrics remain exactly `50 resolved/frozen`, `29 open-blocking`, `8 open-deferable`, total `87`;
8. no unrelated decision, contract, dependency edge or runtime behavior changes.

## Next action

After this reconciliation is integrated, PR #50 can be merged independently when its own review/CI gate is satisfied. A later governance update may then record E05 complete and replace the exhausted implementation frontier with newly frozen decision-derived issues.