# Scope: Post-C03 Governance Reconciliation / ASS-02 Closeout

## Objective

Reconcile canonical governance with the repository state after C03 integration, close the ASS-02 in-process Application vertical, and materialize the already-decided C05 configuration semantics without implementing C05 or ASS-03.

C02 and C03 are both integrated. This reconciliation does not reopen their implementation/review evidence and does not authorize C04, API transport, Job runtime, clients or any other missing capability.

The completed Producer–Consumer Conformity cycle remains credited under ADR-0023: **Contract Freeze Gate = PASSED** and **Conformity Implementation Gate = PASSED**. This slice does not reopen either gate.

## Baseline

```text
base main                  = 066eaf6984fca9d863bc53c9c7fa07c08a1f464c
source integration         = PR #41
reviewed C03 head          = f382a2e6f97ce95f143eed866e602ebf0c5f3acb
exact-head CI              = Quant Platform integrity #91 PASS
mutation class             = governance docs only
source/tests/runtime       = NO
contracts/ADRs             = NO
```

PR #41 integrated the reviewed C03 candidate `f382a2e6f97ce95f143eed866e602ebf0c5f3acb` into `main` as merge commit `066eaf6984fca9d863bc53c9c7fa07c08a1f464c`. The pull-request workflow `Quant Platform integrity #91` passed on that exact candidate head.

C02 evidence from PR #30 and C03 review/CI evidence are credited and must not be re-proved absent a concrete invalidating change.

## Accepted state

```text
C01  Application ownership                    COMPLETE
C02  ASS-02 semantic selector resolution      COMPLETE
C03  ASS-02 result/error translation          COMPLETE
V2   DataGateway -> Application               COMPLETE
C05  Configuration convergence decision       RESOLVED / implementation MISSING
C04  Tool orchestration convergence           RESOLVED / implementation MISSING; requires C05 implementation
```

ASS-02 means the current in-process Application service vertical only. It does not imply API transport, Job runtime, clients, C05 implementation or ASS-03 completion.

## Frozen C05 decision recorded by this reconciliation

The Application configuration boundary is:

```text
CLI / environment
      ↓
executable boundary: acquire + resolve input only
      ↓
typed immutable capability-specific Application config
      ↓
quant_platform.application: concrete composition owner
      ↓
Catalog / DataGateway / source / producer capabilities
```

Rules:

1. `tools/` owns CLI/environment acquisition and parsing.
2. Resolution precedence is **explicit CLI > environment > declared default > explicit failure**.
3. Application receives resolved values only and does not know their acquisition source.
4. Application-facing config is typed, immutable and capability-specific.
5. `quant_platform.application` owns concrete composition and does not read `sys.argv`, `argparse` or environment variables directly.
6. No generic DI container, service locator, provider registry or plugin/config framework is introduced.
7. C05 implementation remains missing; C04 remains blocked until that implementation exists.

## Current execution frontier

Decision-complete, missing atoms whose declared planning dependencies are satisfied:

```text
C05  configuration convergence for the Application service
D03  historical Candle runtime (on-demand only)
E05  H01 pure imbalance kernel
F05  deterministic walk-forward schedule
I01  Study/Trial/Run/Artifact semantic model
K04  observational capacity monitoring
```

Frontier membership remains planning readiness only; it is not implementation authorization.

## Scope

Included:

- record C03 as `FROZEN / COMPLETE` in the canonical DAG and capability map;
- record `C02 + C03 = ASS-02 in-process Application vertical COMPLETE`;
- record V2 as COMPLETE;
- remove C03 from every current-frontier representation;
- record C05 as `RESOLVED / MISSING` with the frozen configuration convention above;
- update planning metrics from `49/30/8` to `50/29/8` while preserving 87 classified atoms;
- expose C05 as the next Application atom and preserve C04 as blocked on C05 implementation;
- preserve all unrelated dependency edges, gate semantics, deferable triggers and macro phases.

Excluded:

- any source, test, runtime, schema, DDL or fixture mutation;
- any ADR/contract semantic change;
- C05 implementation;
- C04 implementation or tool-debt migration;
- API transport, Job runtime or client implementation;
- unrelated decision resolution;
- re-review or re-execution of already-credited C02/C03 proof.

## Preserved invariants

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

The only decision-state movement is `C05: OPEN_BLOCKING -> RESOLVED`. C03 changes implementation state only (`MISSING -> COMPLETE`). No dependency edge, frozen Consumer API semantic, phase numbering or deferable trigger changes.

## Acceptance

DONE means all of the following are true:

1. C03 is `FROZEN / COMPLETE` in canonical governance;
2. C02 + C03 are recorded as ASS-02 COMPLETE and V2 COMPLETE;
3. C05 is `RESOLVED / MISSING` with the exact configuration boundary and precedence above;
4. every current-frontier list is exactly `C05,D03,E05,F05,I01,K04`;
5. C04 remains `RESOLVED / MISSING` and blocked on C05 implementation;
6. planning metrics are exactly `50 resolved/frozen`, `29 open-blocking`, `8 open-deferable`, total `87`;
7. no source/test/runtime/contract/ADR/schema/DDL/fixture mutation is included;
8. credited C03 review and exact-head CI are referenced rather than re-proved.

## Next action

After this governance reconciliation is integrated, issue #33 can close as completed and #34 (`C05 — Configuration convergence`) becomes the next Application atom eligible for the implementation router. #35–#39 remain independent implementation-ready slices according to their existing issue mandates.