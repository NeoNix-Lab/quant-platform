# Scope: Roadmap vNext Capability DAG v1 — GOVERNANCE MATERIALIZATION

## Goal

Materialize the architecture-roadmap atomization into repository governance without authorizing runtime implementation or prematurely freezing deferable future semantics.

Planning authority is layered:

```text
ROADMAP.md                 = macro product progression (phases 0–15)
CAPABILITY_DAG.md          = atomic dependency/execution authority
CAPABILITY_MAP.md          = compact canonical-state snapshot
OPEN_DECISIONS.md          = live decision gates / unresolved decisions
ADRs + contracts           = semantic authority
SCOPE.md                   = current bounded authorization
```

## Baseline

```text
base main                  = 8ae5b1ca36e6ca0e23b330fbf26d7dc529af7dc5
branch                     = governance/roadmap-vnext-capability-dag-v1
mutation class             = governance/docs only
runtime implementation     = NONE
implementation authorized  = NO
```

ASS-01 remains COMPLETE. ASS-02, ASS-03, H01 and all other implementation atoms remain NOT ACTIVE.

The completed Producer–Consumer Conformity cycle governed by ADR-0023 remains credited: **Contract Freeze Gate = PASSED** and **Conformity Implementation Gate = PASSED**. This governance slice does not reopen either gate.

## Roadmap model

The corrected inventory is:

```text
TOTAL_ATOMS                     = 87
CLASSIFIED_ATOMS                = 87
UNCLASSIFIED_GAPS               = 0
SEMANTIC_FROZEN_OR_RESOLVED     = 49 / 87 = 56.3%
OPEN_BLOCKING                   = 30
OPEN_DEFERABLE                  = 8
ROADMAP_DEFINED                 = 49 + 30 + 8 = 87
ROADMAP_PLANNING_COMPLETENESS   = 87 / 87 = 100%
```

This score is reproducible from `CAPABILITY_DAG.md` and has a deliberately different meaning from semantic freeze. A blocker counts as roadmap-defined only when it is assigned to an atom-specific decision path that activates before the dependent implementation. A deferable counts only when its real evidence trigger is explicit.

The 56.3% semantic-freeze percentage is intentionally not used as a requirement to freeze future choices early.

## Dependency integrity

The corrected `Requires` graph must remain acyclic.

Two sequencing rules are explicit:

```text
A11 live acquisition -> K10 checkpoint/recovery implementation
K06 source protection -> K08 backup/restore -> K09 deletion authority
```

A11 therefore does not require implemented K10. K10 follows A11 and adds checkpoint/recovery behavior to the live capability. K06 does not require K08, and backup/restore does not require tier relocation.

## Decision-gate model

Thirty blocking future decisions are grouped into gate **families**:

```text
DG-A  Representation / Feature integration
DG-B  Historical / Live data convergence
DG-C  Market-data depth (L1/L2)
DG-D  Application configuration / ASS-03
DG-E  Validation semantics
DG-F  Strategy / Execution semantics
DG-G  Experiment / RL / Jobs
DG-H  Operational safety
```

A gate-family name is not a monolithic prerequisite. Selecting an atom activates only unresolved propositions on that atom's transitive dependency path.

Required granular distinctions include:

```text
DG-A: D05 Candle materialization is independent from the E06 canonical-H01 path.
DG-B: historical repair does not automatically activate live-cursor semantics.
DG-C: selecting L1 does not activate L2.
DG-E: F08 DSR/PBO does not block the F06/F07 Validation -> Strategy/ML path.
DG-G/DG-H: independent/progressive sub-gates remain independently activated.
```

Eight explicitly deferable decisions remain open until real evidence exists: second-provider capability resolution, exact L3/MBO semantics, DatasetSnapshot shape, future schema evolution, provider-extension mechanism, multi-asset execution and concrete API transport.

## Execution frontier

```text
C02  ASS-02 semantic selector resolution
D03  historical Candle runtime (on-demand only)
E05  H01 pure imbalance kernel
F05  deterministic walk-forward schedule
I01  Study/Trial/Run/Artifact semantic model
K04  observational capacity monitoring
K11  governance-state consistency
```

This is a **frontier, not implementation authorization** and does not select a next atom.

## Macro roadmap

Phases 0–15 remain the product-progression view and are not renumbered.

```text
Phase          = product progression
Atom           = executable capability proposition
Decision Gate  = atom-specific semantic/architecture authorization
Vertical       = cross-spine integration proof
Wave           = planning grouping
```

The DAG, not phase numbering, determines executable dependency order.

## Governance-only scope

Included:

- add and correct `docs/product/CAPABILITY_DAG.md`;
- keep the 87-atom inventory finite and acyclic;
- link the macro roadmap to the DAG and remove false linear dependencies;
- keep `CAPABILITY_MAP.md` concise with separate decision/implementation state;
- organize open decisions by atom-specific DG-A…DG-H activation;
- record the explicit planning-readiness formula and execution frontier;
- correct directly relevant non-semantic governance drift when safe.

Excluded:

- runtime/source/test/tool/schema/DB/fixture changes;
- ASS-02/ASS-03/H01 implementation;
- new package ownership for future bounded contexts;
- transport or service-topology decisions;
- speculative DI/plugin/provider frameworks;
- freezing L1/L2/L3, RL, live, storage or execution semantics without their trigger/evidence.

## Acceptance

This governance slice is ready for review when:

1. the 87-atom inventory is finite and all atoms are classified;
2. every atom has owner, dependencies, unlocks, decision state, implementation state and acceptance/authority;
3. the complete `Requires` graph is acyclic;
4. all 30 open blockers are covered by atom-specific activation paths without false sibling prerequisites;
5. all eight deferable decisions have explicit evidence triggers;
6. planning completeness is reproducibly `87/87 = 100%` under the documented definition;
7. phases 0–15 remain the macro roadmap;
8. `ROADMAP.md`, `CAPABILITY_MAP.md`, `OPEN_DECISIONS.md` and this scope reference one atomic authority rather than competing with it;
9. no implementation atom is activated;
10. no accepted ADR/contract semantics are changed.

## Next action after merge

If independent review approves this governance model and it is merged, Roadmap vNext becomes canonical. Only then select together one bounded atom or decision gate from the current frontier based on value, dependency and risk.
