# Quant Platform Roadmap

This document is the **macro product-progression view**. The authoritative atomic dependency/execution model is [`CAPABILITY_DAG.md`](CAPABILITY_DAG.md). Exact semantic authority remains in accepted ADRs/contracts; `SCOPE.md` activates bounded work.

## Planning layers

```text
ROADMAP.md          = product progression
CAPABILITY_DAG.md   = atomic capability dependencies + decision gates + verticals
CAPABILITY_MAP.md   = compact current-state snapshot
OPEN_DECISIONS.md   = unresolved decisions / atom-specific gate triggers
ADRs + contracts    = semantic authority
SCOPE.md            = active bounded authorization
```

A numbered phase is not an executable work packet. Parallel work is allowed when Capability DAG dependencies are satisfied.

## Macro phases 0–15

0. Repository and governance foundation
1. Data access and identity foundation: DataGateway, catalog-backed reads and provenance
2. Representation foundation: representation identity, CandleDefinition and deterministic historical/live semantics
3. Feature foundation: FeatureDefinition, FeatureSetDefinition, FeatureArtifact, provenance, providers and materialization
4. Research and outcome foundation: Hypothesis/Event/Outcome identity, reproducibility, event studies and sweeps
5. Validation and labeling: availability, warmup, walk-forward, purge, embargo, lockbox and censoring
6. Strategy and decision foundation: StrategySpec, policies, DecisionIntent, risk and sizing
7. Execution, replay and portfolio: orders, fills, positions, accounting and deterministic replay
8. Unified Experiment Orchestration and Persistence: Study, Trial, Run and Artifact identity/persistence/comparison/reproduction
9. Canonical API and job runtime
10. Supervised ML
11. Strategic RL
12. Execution RL
13. Clients: CLI, TUI and App UI
14. Paper and shadow operation
15. Live operation

These phases remain intentionally stable. The DAG determines actual dependency order inside and across them.

## Completed foundation checkpoints

```text
Contract Freeze Gate                                  PASSED
Conformity Implementation Gate                        PASSED
Human / Golden Bybit BTCUSDT E2E                     PASS
Adversarial Acceptance A1-A9                         PASS
Candle Ordering Compatibility                        PASS
Package Boundary / Modular Monolith Foundation v1   COMPLETE
Legacy Capability Harvest Audit v1                   COMPLETE
ASS-01 Application ownership/enforcement             COMPLETE
ASS-02 C02 semantic selector resolution              COMPLETE
K11 Governance-state consistency                     COMPLETE
Broad independent Producer/Consumer expansion        UNLOCKED
```

The two-stage Producer–Consumer Conformity Gate is governed by [ADR-0023](../decisions/ADR-0023-producer-consumer-conformity-gate-v1.md): the **Contract Freeze Gate** and **Conformity Implementation Gate** remain PASSED. The gate pair is concluded and is referenced here as credited foundation evidence, not reopened work.

Package Boundary established modular-monolith ownership/dependency rules. Legacy Harvest classified 31 capabilities and retained H01 as the first **legacy harvest candidate** without activating it. ASS-01 established `quant_platform.application` as the in-process composition owner without adding application use-case runtime, API transport, Job runtime or clients. C02 then established the frozen `trades@1` semantic selector-resolution seam through PR #30; C03 result/error translation remains the missing half of ASS-02. Roadmap vNext governance-state consistency is complete after PR #28 established the coherent canonical authority set on `main`.

Completed checkpoints are credited and must not be re-proved absent a concrete invalidating change.

## Roadmap vNext planning state

The corrected architecture-roadmap inventory is:

```text
TOTAL_ATOMS                     87
CLASSIFIED_ATOMS                87
UNCLASSIFIED_GAPS               0
SEMANTIC_FROZEN_OR_RESOLVED     49 / 87 = 56.3%
OPEN_BLOCKING                   30
OPEN_DEFERABLE                  8
ROADMAP_DEFINED                 49 + 30 + 8 = 87
ROADMAP_PLANNING_COMPLETENESS   87 / 87 = 100%
```

The 100% planning score means every roadmap atom is classified: frozen/resolved, assigned to an atom-specific blocking gate, or explicitly deferable with a real evidence trigger. It does **not** mean every future semantic choice is frozen.

The 56.3% value remains the semantic-freeze metric. Future provider, live, RL, transport and operational choices are not frozen merely to increase it.

## Decision-gate model

Open blockers are grouped into gate families, but activation is **atom-path specific**:

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

Important non-monolithic boundaries:

- DG-A: Candle materialization `D05` is independent from the canonical-H01 path `D06/E02/E04/E06`.
- DG-B: repair semantics do not automatically activate live cursor semantics.
- DG-C: L1 may be selected without activating L2.
- DG-E: DSR/PBO `F08` does not block Strategy/ML paths that depend on `F07`.
- DG-G and DG-H retain independent/progressive branches.

Explicit deferables — second-provider resolution, exact L3/MBO semantics, DatasetSnapshot shape, future schema evolution, generic provider extension, multi-asset execution and concrete API transport — remain open until their evidence trigger exists.

## Corrected operational dependency direction

The DAG is acyclic. In particular:

```text
A11 live acquisition -> K10 checkpoint/recovery implementation
K06 source protection -> K07 relocation
K06 source protection -> K08 backup/restore -> K09 deletion authority
```

Implemented recovery is therefore not a prerequisite of implementing the live capability it checkpoints. Backup/restore does not require tier-relocation implementation.

## Vertical milestones

```text
V1  Source -> canonical -> catalog -> DataGateway         COMPLETE
V2  DataGateway -> Application service                   C03 READY (C02 COMPLETE)
V3  DataGateway -> historical Candle                     READY
V4  Representation -> Feature -> canonical H01           BLOCKED by DG-A canonical-H01 branch
V5  Feature -> Research                                  BLOCKED by FeatureArtifact
V6  Research -> Validation                               BLOCKED by DG-E validation branch
V7  Strategy -> deterministic Replay                     BLOCKED by DG-F
V8  Historical -> Live                                   BLOCKED by DG-B live branch + relevant DG-H; DAG acyclic
V9  Application -> API -> Client                         application-ready; transport deferred
V10 Paper/Shadow -> Live                                 BLOCKED by runtime/operational gates
```

V1 is the accepted Bybit BTCUSDT first vertical; its detailed evidence remains in existing conformity/integration documentation. V2 is not complete: C02 is integrated, while C03 remains the ready missing atom.

## Current execution frontier

Decision-complete, missing atoms whose declared planning dependencies are satisfied:

```text
C03  ASS-02 result/error translation
D03  historical Candle runtime (on-demand only)
E05  H01 pure imbalance kernel
F05  deterministic walk-forward schedule
I01  Study/Trial/Run/Artifact semantic model
K04  observational capacity monitoring
```

Frontier membership is **not implementation authorization** and does not select the next atom.

## Execution waves

Waves are dependency/value groupings, not a new linear phase numbering.

```text
Wave 0  Architecture foundation                           COMPLETE
Wave 1  First canonical computation verticals             current frontier
Wave 2  Representation / Feature                          atom-specific DG-A branches
Wave 3  Research / Validation                             F01-F04 then DG-E validation path
Wave 4  Strategy / Replay                                 G01-G03 then DG-F/H01-H05
Wave 5  Experiment / Supervised ML                       I01, DG-G experiment branch, I03-I05
Wave 6  Live Data Plane                                   K04 + relevant DG-H, then DG-B repair/live paths
Wave 7  Runtime / Clients                                 C03 -> J02 -> thin clients when real client need exists
Wave 8  Paper / Live product                             J07 -> J08 after required data/execution/ops gates
```

Parallelism is allowed whenever DAG dependencies are satisfied. In particular, C03, historical Candle, pure H01, walk-forward semantics, experiment identity and observational capacity can be scoped independently.

## Producer / storage interpretation

The former producer-side narrative was too linear. Capacity observation, application work, historical representation work, backup design and other independent capabilities may advance in parallel when their declared dependencies are met.

Backfill/repair does not require the entire storage-tiering program. Backup/restore must precede deletion authority but need not wait for tier relocation. L1/L2 contract work requires real feed evidence, not a second venue. API transport does not precede in-process ASS-02.

## Planning rule

Before opening implementation:

1. verify `origin/main`;
2. select one atom from the current frontier or one required decision branch;
3. verify its transitive `Requires` path;
4. activate only unresolved decisions on that path;
5. credit existing evidence;
6. define minimum acceptance/proof;
7. create a bounded scope/branch;
8. keep unrelated frontier atoms inactive.

Roadmap state never authorizes production/runtime mutation by itself.
