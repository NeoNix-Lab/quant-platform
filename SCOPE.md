# Scope: C03 / ASS-02 Result & Error Translation

## Objective

Reconcile canonical governance after C02 integration, complete the read-only audit for atom `C03 — ASS-02 result/error translation`, and authorize its bounded implementation.

C02 is already implemented and integrated through PR #30. This scope does not reopen C02 and does not activate ASS-03, API transport, Job runtime or any other frontier atom.

The completed Producer–Consumer Conformity cycle remains credited under ADR-0023: **Contract Freeze Gate = PASSED** and **Conformity Implementation Gate = PASSED**. This slice does not reopen either gate.

## Baseline

```text
base main                  = 484eb53504cd80842b41675b04206f8b80482b85
branch                     = implementation/application-service-ass-02-result-error-v1
mutation class             = local source + tests + SCOPE
C03 audit                  = COMPLETE
C03 implementation         = AUTHORIZED
commit                     = authorized
push                       = NO
PR                         = NO
```

PR #30 integrated the reviewed C02 candidate `3a07b913f5d825706a959a110fb92b9199c3c5eb` into `main` as merge commit `484eb53504cd80842b41675b04206f8b80482b85`. The exact-head CI for that PR passed.

## Accepted state

```text
C01  Application ownership                    COMPLETE
C02  ASS-02 semantic selector resolution      COMPLETE
C03  ASS-02 result/error translation           MISSING
V2   DataGateway -> Application                C03 READY; not COMPLETE
```

C02 evidence is credited and must not be re-proved absent a concrete invalidating change.

## Current execution frontier

```text
C03  ASS-02 result/error translation
D03  historical Candle runtime (on-demand only)
E05  H01 pure imbalance kernel
F05  deterministic walk-forward schedule
I01  Study/Trial/Run/Artifact semantic model
K04  observational capacity monitoring
```

Frontier membership remains planning readiness only; it is not implementation authorization.

## Scope

Included:

- reconcile `CAPABILITY_DAG.md`, `CAPABILITY_MAP.md` and `ROADMAP.md` with the integrated C02 state;
- record C02 as `FROZEN / COMPLETE`;
- remove C02 from the current frontier and expose C03 as the next Application atom on V2;
- preserve all atom dependencies, decision states, gate rules and macro phases;
- record the completed read-only C03 audit against the frozen Consumer API/DataGateway authority;
- implement C03 result/error translation over an injected access capability, within the
  audited boundary: `scan()` batch seam, consumer-owned request identity, allowlisted
  provenance, the six frozen error codes and application-owned safe error context.

Excluded:

- any ADR/contract semantic change;
- Capability DAG/Map/Roadmap mutation during implementation;
- gateway, catalog, connection, DSN or configuration construction (C05);
- C04/C05/C06 implementation or decision changes;
- API transport, Job runtime, clients, Candle/Feature work or other frontier atoms;
- schema, DDL, fixture, package, runtime, database or server mutation.

## Preserved invariants

```text
TOTAL_ATOMS                     = 87
CLASSIFIED_ATOMS                = 87
UNCLASSIFIED_GAPS               = 0
SEMANTIC_FROZEN_OR_RESOLVED     = 49 / 87 = 56.3%
OPEN_BLOCKING                   = 30
OPEN_DEFERABLE                  = 8
ROADMAP_PLANNING_COMPLETENESS   = 87 / 87 = 100%
REQUIRES_EDGES                  = 157
CYCLES                          = 0
```

No atom dependency, decision state, gate activation rule, frozen acceptance proposition or phase numbering changes in this reconciliation.

## Acceptance

DONE means all of the following are true:

1. C02 is `FROZEN / COMPLETE` in the canonical DAG and capability map;
2. C03 remains `FROZEN / MISSING`;
3. every current-frontier list is exactly `C03,D03,E05,F05,I01,K04`;
4. V2 records C02 complete and C03 ready without claiming V2 or ASS-02 complete;
5. roadmap metrics and dependency/gate semantics remain unchanged;
6. no tool, contract, ADR, schema, DDL or fixture file changes;
7. C03 source and test changes stay inside the audited minimum surface.

## Next action

The C03 audit returned `C03_IMPLEMENTATION_READY`: execution is owned by Application over an
injected gateway, boundedness comes from the caller's explicit finite interval, and no C05,
C06, J02 or J03 decision is required. Implement C03 within that boundary, then submit the
candidate for independent review. C03 is not COMPLETE until that review approves it.
