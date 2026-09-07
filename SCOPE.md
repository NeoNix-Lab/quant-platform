# Scope: Roadmap vNext Capability DAG v1 — GOVERNANCE MATERIALIZATION

## Goal

Materialize the accepted architecture-roadmap atomization audit into repository governance without authorizing runtime implementation or prematurely freezing deferable future semantics.

This slice establishes a layered planning model:

```text
ROADMAP.md                 = macro product progression (phases 0–15)
CAPABILITY_DAG.md          = atomic dependency/execution authority
CAPABILITY_MAP.md          = compact canonical-state snapshot
OPEN_DECISIONS.md          = live decision gates / unresolved decisions
ADRs + contracts           = semantic authority
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

## Roadmap model

The audit classified the full planning inventory:

```text
TOTAL_ATOMS                     = 87
CLASSIFIED_ATOMS                = 87
UNCLASSIFIED_GAPS               = 0
SEMANTIC_FROZEN_OR_RESOLVED     = 49 / 87 = 56.3%
OPEN_BLOCKING                   = 30
OPEN_DEFERABLE                  = 8
ROADMAP_PLANNING_COMPLETENESS   = >= 90%
```

The semantic-freeze percentage is intentionally not used as a requirement to freeze future choices early. A deferable decision is planning-complete when its trigger/evidence boundary is explicit.

The canonical proposed atom/dependency model is `docs/product/CAPABILITY_DAG.md`.

## Decision gates

Thirty blocking future decisions are grouped by activation boundary rather than scheduled as an up-front design program:

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

A gate is activated only when a selected dependent atom requires it.

Eight explicitly deferable decisions remain open until real evidence exists, including second-provider capability resolution, exact L3/MBO semantics, DatasetSnapshot shape, future schema evolution, provider-extension mechanism, multi-asset execution and concrete API transport.

## Execution frontier

The current classified frontier is:

```text
C02  ASS-02 semantic selector resolution
D03  historical Candle runtime (on-demand only)
E05  H01 pure imbalance kernel
F05  deterministic walk-forward schedule
I01  Study/Trial/Run/Artifact semantic model
K04  observational capacity monitoring
K11  governance-state consistency
```

This list is **not implementation authorization** and does not select a next atom.

## Macro roadmap

Phases 0–15 remain the product-progression view and are not renumbered.

```text
Phase          = product progression
Atom           = executable capability proposition
Decision Gate  = semantic/architecture authorization
Vertical       = cross-spine integration proof
Wave           = planning grouping
```

The DAG, not phase numbering, determines executable dependency order.

## Governance-only scope

Included:

- add `docs/product/CAPABILITY_DAG.md`;
- link the macro roadmap to the DAG and replace false linear dependencies with DAG semantics;
- keep `CAPABILITY_MAP.md` concise while separating decision state from implementation state where the aggregate view was misleading;
- organize open decisions by DG-A…DG-H and explicit deferable triggers;
- record the roadmap-readiness model and execution frontier;
- correct directly relevant non-semantic governance drift when safe.

Excluded:

- runtime/source/test/tool/schema/DB/fixture changes;
- ASS-02/ASS-03/H01 implementation;
- new package ownership for future bounded contexts;
- transport or service-topology decisions;
- speculative DI/plugin/provider frameworks;
- freezing L1/L2/L3, RL, live, storage or execution semantics without the decision gate trigger/evidence.

## Acceptance

This governance slice is ready for review when:

1. the 87-atom inventory is finite and all atoms are classified;
2. every atom has owner, dependency state, implementation state and acceptance proposition;
3. the DAG has no planning-level circular dependency;
4. DG-A…DG-H cover the open blockers without forcing premature resolution;
5. all deferable decisions have an explicit evidence trigger;
6. phases 0–15 remain the macro roadmap;
7. `ROADMAP.md`, `CAPABILITY_MAP.md`, `OPEN_DECISIONS.md` and this scope reference one atomic authority rather than duplicating it;
8. no implementation atom is activated;
9. no accepted ADR/contract semantics are changed.

## Next action after merge

If independent review approves this governance model and it is merged, Roadmap vNext becomes canonical. Only then select together one bounded atom or decision gate from the current frontier based on value, dependency and risk.
