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
ASS-02 in-process Application service (C02 + C03)    COMPLETE
C05 Application configuration convergence            COMPLETE
ASS-03 tool orchestration convergence (C04)           COMPLETE
D03 historical Candle computation                    COMPLETE
E05 H01 pure imbalance kernel                        COMPLETE
F05 deterministic walk-forward schedule              COMPLETE
I01 experiment semantic identity model               COMPLETE
K04 observational capacity                           COMPLETE
K11 Governance-state consistency                     COMPLETE
Broad independent Producer/Consumer expansion        UNLOCKED
```

The two-stage Producer–Consumer Conformity Gate is governed by [ADR-0023](../decisions/ADR-0023-producer-consumer-conformity-gate-v1.md): the **Contract Freeze Gate** and **Conformity Implementation Gate** remain PASSED. The gate pair is concluded and is referenced here as credited foundation evidence, not reopened work.

Package Boundary established modular-monolith ownership/dependency rules. Legacy Harvest classified 31 capabilities and retained H01 as the first **legacy harvest candidate**. ASS-01 established `quant_platform.application` as the in-process composition owner. C02 and C03 completed the frozen ASS-02 semantic Application vertical. PR #43 integrated C05's typed immutable configuration/composition seam, and PR #44 removed the finite ASS-03 tool-orchestration debt through that canonical Application seam. PRs #46–#49 integrated K04, D03, F05 and I01 respectively. PR #50 / E05 is treated as closed for sequencing by explicit operator instruction; this governance state must not merge into `main` until actual E05 integration is verified.

Completed checkpoints are CREDIT and must not be re-proved absent a concrete invalidating change.

## Roadmap vNext planning state

The architecture-roadmap inventory remains:

```text
TOTAL_ATOMS                     87
CLASSIFIED_ATOMS                87
UNCLASSIFIED_GAPS               0
SEMANTIC_FROZEN_OR_RESOLVED     50 / 87 = 57.5%
OPEN_BLOCKING                   29
OPEN_DEFERABLE                  8
ROADMAP_DEFINED                 50 + 29 + 8 = 87
ROADMAP_PLANNING_COMPLETENESS   87 / 87 = 100%
```

The 100% planning score means every roadmap atom is classified: frozen/resolved, assigned to an atom-specific blocking gate, or explicitly deferable with a real evidence trigger. It does **not** mean every future semantic choice is frozen.

The 57.5% value is the current semantic-freeze/resolution metric. The implementation completions recorded by this closeout do not alter that metric because no decision-state changes occurred.

## Decision-gate model

Open blockers are grouped into gate families, while resolved gates remain as historical references. Activation is **atom-path specific**:

```text
DG-A  Representation / Feature integration
DG-B  Historical / Live data convergence
DG-C  Market-data depth (L1/L2)
DG-D  Application configuration / ASS-03      RESOLVED / COMPLETE
DG-E  Validation semantics
DG-F  Strategy / Execution semantics
DG-G  Experiment / RL / Jobs
DG-H  Operational safety
```

Important non-monolithic boundaries:

- DG-A: Candle materialization `D05` is independent from the canonical-H01 path `D06/E02/E04/E06`.
- DG-B: repair semantics do not automatically activate live cursor semantics.
- DG-C: L1 may be selected without activating L2.
- DG-D: C05 configuration/composition and C04/ASS-03 tool convergence are complete; DG-D remains historical reference, not a live blocker.
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
V2  DataGateway -> Application service                   COMPLETE (C02 + C03)
V3  DataGateway -> historical Candle                     COMPLETE (D03)
V4  Representation -> Feature -> canonical H01           BLOCKED by D06 + E02 + E04 + E06; E05 kernel COMPLETE
V5  Feature -> Research                                  BLOCKED by FeatureArtifact
V6  Research -> Validation                               BLOCKED by DG-E validation branch
V7  Strategy -> deterministic Replay                     BLOCKED by DG-F
V8  Historical -> Live                                   BLOCKED by DG-B live branch + relevant DG-H; DAG acyclic
V9  Application -> API -> Client                         application + executable convergence complete; transport deferred
V10 Paper/Shadow -> Live                                 BLOCKED by runtime/operational gates
```

V1 is the accepted Bybit BTCUSDT first vertical. V2 is complete at the in-process semantic service boundary. C05 and C04/ASS-03 establish the canonical executable-to-Application composition path, but do not imply API transport, Job runtime or clients. V3 is complete because bounded canonical trades produce reproducible CLOSED candles through D03. E05 completes only the pure accepted H01 quantitative kernel; canonical H01 remains blocked on its Representation/Feature integration path.

## Current implementation frontier

The original decision-complete ready-slice frontier is exhausted:

```text
IMPLEMENTATION_FRONTIER = empty
```

There is no additional implementation atom to route merely because coding capacity is available. New implementation supply must first come from bounded resolution of selected `OPEN_BLOCKING` propositions.

## Selected next decision backlog

The next decision work selected for issue materialization is:

```text
E02  FeatureDefinition v1 semantics                 DG-A
D06  Footprint representation semantics             DG-A
K05  Health / pressure policy                       DG-H
A16  General quality lifecycle                      DG-B
B04  Non-contiguous coverage read                   DG-B
I02  Experiment persistence model                   DG-G
K03  Minimum observability contract                 DG-H
```

These atoms remain `OPEN_BLOCKING`. Selection means they are the next propositions to resolve; it does not change their state and does not authorize implementation before their individual decision is frozen.

Likely downstream supply after those decisions includes E04/E06/F06, A10/K06 and I03/J03 according to the declared DAG. Those downstream atoms are not pre-authorized.

## Execution waves

Waves are dependency/value groupings, not a new linear phase numbering.

```text
Wave 0  Architecture foundation                           COMPLETE
Wave 1  First canonical computation/application slices    COMPLETE
Wave 2  Representation / Feature                          E02 + D06 decisions, then E04/E06 path
Wave 3  Research / Validation                             F01-F04 and DG-E validation path
Wave 4  Strategy / Replay                                 G01-G03 then DG-F/H01-H05
Wave 5  Experiment / Supervised ML                       I01 complete; I02 decision next, then I03-I05
Wave 6  Live Data Plane                                   K04 complete; K05/A16/B04 next, then protection/repair/live
Wave 7  Runtime / Clients                                 J02 -> thin clients when real client need exists
Wave 8  Paper / Live product                             J07 -> J08 after required data/execution/ops gates
```

Parallelism is allowed whenever DAG dependencies are satisfied. The next supply wave deliberately mixes independent DG-A, DG-B, DG-G and DG-H decisions so implementation agents can be fed as soon as each branch is frozen, without waiting for an unrelated branch.

## Producer / storage interpretation

The producer/server path is intentionally progressive rather than monolithic. K05 pressure policy can advance from completed K04 while A16/B04 resolve only the repair/coverage semantics needed for A10. K06 then follows the selected source plus K05; K08 follows K06. This preserves the acyclic direction toward A11 live acquisition.

Backfill/repair does not require the entire storage-tiering program. Backup/restore must precede deletion authority but need not wait for tier relocation. L1/L2 contract work requires real feed evidence, not a second venue. API transport does not precede the completed in-process ASS-02/ASS-03 Application path.

## Planning rule

Before opening implementation:

1. verify `origin/main`;
2. select one atom from a frozen implementation frontier or one required decision branch;
3. verify its transitive `Requires` path;
4. activate only unresolved decisions on that path;
5. credit existing evidence;
6. define minimum acceptance/proof;
7. create a bounded scope/issue/branch;
8. keep unrelated branches inactive.

Roadmap state never authorizes production/runtime mutation by itself.