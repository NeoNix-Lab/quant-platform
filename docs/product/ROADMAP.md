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
K11 Governance-state consistency                     COMPLETE
E02 FeatureDefinition v1 semantic foundation         COMPLETE
D06 FootprintDefinition v1 representation           COMPLETE
B04 Non-contiguous coverage reads v1                COMPLETE
K06 RAW / source protection v1                       COMPLETE
A10 Backfill / repair v1                             COMPLETE
E04 FeatureArtifact v1                               COMPLETE
E06 H01 canonical integration                        COMPLETE (ADR-0035; issue #83/PR #88)
F02 EventSpec/detection                              COMPLETE (issue #85/PR #89)
F03 OutcomeSpec/Outcome                              COMPLETE (issue #86/PR #90; ADR-0038 + issue #102/PR #103 closeout)
F04 Event studies and parameter sweep runtime v1     COMPLETE (issue #92/PR #94)
F07 labels/censoring/lockbox                         COMPLETE (ADR-0036; issue #96/PR #99)
F08 DSR/PBO robust comparison                        COMPLETE (ADR-0037; issue #98/PR #104)
Wave 1 implementation batch (implement/wave-1)       CONCLUDED (2026-09-19)
Wave 2 implementation batch (implement/wave-2)       COMPLETE (E06,F02,F03; issue #87 reconciliation)
Wave 3 implementation batch (implement/wave-3)       COMPLETE (F01-F08; final governance reconciliation)
Broad independent Producer/Consumer expansion        UNLOCKED
```

The two-stage Producer–Consumer Conformity Gate is governed by [ADR-0023](../decisions/ADR-0023-producer-consumer-conformity-gate-v1.md): the **Contract Freeze Gate** and **Conformity Implementation Gate** remain PASSED. The gate pair is concluded and is referenced here as credited foundation evidence, not reopened work.

Package Boundary established modular-monolith ownership/dependency rules. Legacy Harvest classified 31 capabilities and retained H01 as the first **legacy harvest candidate** without activating it. ASS-01 established `quant_platform.application` as the in-process composition owner without adding application use-case runtime, API transport, Job runtime or clients. C02 established the frozen `trades@1` semantic selector-resolution seam through PR #30; C03 completed the frozen result/error translation seam through PR #41 after exact-head `Quant Platform integrity #91` PASS. Together C02 + C03 complete the current in-process ASS-02 Application vertical. C05 configuration convergence and C04/ASS-03 tool orchestration convergence are both complete (C04 via issue #40/PR #44, 2026-09-14). Roadmap vNext governance-state consistency remains a credited foundation capability after the post-C03 reconciliation. E02 FeatureDefinition v1 is frozen and complete under ADR-0026. D06 FootprintDefinition v1 is frozen and complete under ADR-0027. B04 non-contiguous coverage reads v1 is frozen and complete under ADR-0029. K06 RAW/source protection v1 is frozen and complete under ADR-0032. A10 backfill/repair v1 is frozen and complete under ADR-0033. E04 FeatureArtifact v1 is frozen and complete under ADR-0034. E06 H01 canonical integration semantics are frozen under ADR-0035 and implementation is complete (issue #83/PR #88). Wave 2 completed E06/F02/F03 and was reconciled by issue #87. On `implement/wave-3`, F04 Event studies/sweeps v1 completed via issue #92/PR #94. ADR-0038 then reconciled F03 Outcome v1 semantic authority and the adversarial EOD/sentinel findings via issue #102/PR #103. F07 is frozen under ADR-0036 and implemented via issue #96/PR #99. F08 is frozen under ADR-0037 and implemented via issue #98/PR #104. Wave 3 is therefore implementation-complete through F08; this reconciliation updates the planning projections without selecting the next scope.

Completed checkpoints are credited and must not be re-proved absent a concrete invalidating change.

## Roadmap vNext planning state

The corrected architecture-roadmap inventory is:

```text
TOTAL_ATOMS                     87
CLASSIFIED_ATOMS                87
UNCLASSIFIED_GAPS               0
SEMANTIC_FROZEN_OR_RESOLVED     64 / 87 = 73.6%
OPEN_BLOCKING                   15
OPEN_DEFERABLE                  8
ROADMAP_DEFINED                 64 + 15 + 8 = 87
ROADMAP_PLANNING_COMPLETENESS   87 / 87 = 100%
IMPLEMENTATION_COMPLETE         45 / 87 = 51.7%
```

The 100% planning score means every roadmap atom is classified: frozen/resolved, assigned to an atom-specific blocking gate, or explicitly deferable with a real evidence trigger. It does **not** mean every future semantic choice is frozen.

The 73.6% value is the current semantic-freeze/resolution metric. Future provider, live, RL, transport and operational choices are not frozen merely to increase it.

## Decision-gate model

Open blockers are grouped into gate families, while resolved gates remain as historical references. Activation is **atom-path specific**:

```text
DG-A  Representation / Feature integration
DG-B  Historical / Live data convergence
DG-C  Market-data depth (L1/L2)
DG-D  Application configuration / ASS-03      RESOLVED for C05 semantics
DG-E  Validation semantics                   RESOLVED and implemented through F07/F08
DG-F  Strategy / Execution semantics
DG-G  Experiment / RL / Jobs
DG-H  Operational safety
```

Important non-monolithic boundaries:

- DG-A: Candle materialization `D05` is independent from canonical H01. `D06`, `E02`, `E04`, E05 and E06 are complete/frozen as recorded by their authorities.
- DG-B: B04 disjoint historical coverage reads, A16 and A10 repair are complete for the accepted first vertical; repair semantics do not automatically activate live cursor semantics.
- DG-C: L1 may be selected without activating L2.
- DG-D: C05 semantics and implementation are complete; C04/ASS-03 is also complete.
- DG-E: F07 is frozen under ADR-0036 and complete via PR #99; F08 is frozen under ADR-0037 and complete via PR #104. F07 completion removes the implementation blocker from G01/I04; F08 remains an independent robustness capability.
- DG-G: I02 experiment persistence is complete.
- DG-H: K03, K04, K05 and K06 are complete.

Explicit deferables — second-provider resolution, exact L3/MBO semantics, DatasetSnapshot shape, future schema evolution, generic provider extension, multi-asset execution and concrete API transport — remain open until their evidence trigger exists.

## Corrected operational dependency direction

The DAG is acyclic. In particular:

```text
A11 live acquisition -> K10 checkpoint/recovery implementation
K06 source protection -> K07 relocation
K06 source protection -> K08 backup/restore -> K09 deletion authority
```

Implemented recovery is therefore not a prerequisite of implementing the live capability it checkpoints. Backup/restore does not require tier relocation.

## Vertical milestones

```text
V1  Source -> canonical -> catalog -> DataGateway         COMPLETE
V2  DataGateway -> Application service                   COMPLETE (C02 + C03)
V3  DataGateway -> historical Candle                     COMPLETE (D03)
V4  Representation -> Feature -> canonical H01           COMPLETE (E06; ADR-0035, issue #83/PR #88)
V5  Feature -> Research                                  COMPLETE (F02/F03/F04; issues #85/#86/#92, PR #89/#90/#94; F03 authority reconciled by ADR-0038/PR #103)
V6  Research -> Validation                               COMPLETE (F06/F07; ADR-0031/ADR-0036, issue #96/PR #99)
V7  Strategy -> deterministic Replay                     BLOCKED by DG-F / upstream implementation
V8  Historical -> Live                                   BLOCKED by DG-B live branch + relevant DG-H; DAG acyclic
V9  Application -> API -> Client                         application service complete; transport deferred
V10 Paper/Shadow -> Live                                 BLOCKED by runtime/operational gates
```

V1 is the accepted Bybit BTCUSDT first vertical. V2 is complete at the in-process semantic service boundary. V5 is complete through F04, with F03's semantic authority reconciled by ADR-0038. V6 is complete: F06 provides leakage-safe availability/purge/embargo and F07 now provides the canonical outcome-derived label/censoring/lockbox runtime. F08 is also complete, but remains an independent robust-comparison capability rather than part of the V6 completion proposition.

## Current execution frontier

Wave 3 is complete. This roadmap does not select the next bounded scope.

F07 completion makes `G01` and `I04` dependency-ready, subject to their own remaining authority/scope. Separately, the live-data path remains governed by its own blockers: `A11` is still `OPEN_BLOCKING / MISSING` and requires the selected DG-B live semantics plus the declared operational prerequisites on that path, including `K08`.

Frontier membership/readiness is **not implementation authorization** and does not select or authorize unrelated atoms. The next bounded scope must be chosen separately.

## Execution waves

Waves are dependency/value groupings, not a new linear phase numbering, and are distinct from temporary `implement/wave-*` integration branches.

```text
Wave 0  Architecture foundation                           COMPLETE
Wave 1  First canonical computation/application slices    COMPLETE (C05,D03,E05,F05,I01,K04)
Wave 2  Representation / Feature                          COMPLETE (E04,E06; ADR-0034/ADR-0035)
Wave 3  Research / Validation                             COMPLETE (F01-F08; ADR-0031/ADR-0036/ADR-0037/ADR-0038)
Wave 4  Strategy / Replay                                 G01-G03 then DG-F/H01-H05
Wave 5  Experiment / Supervised ML                       I01,I02 COMPLETE; I03-I05 remain
Wave 6  Live Data Plane                                   K03,K04,K05,K06,A10,A16 COMPLETE; K02,K07-K10,A11,B06 remain
Wave 7  Runtime / Clients                                 J02 -> thin clients when real client need exists
Wave 8  Paper / Live product                             J07 -> J08 after required data/execution/ops gates
```

Parallelism is allowed whenever DAG dependencies are satisfied. Completion of Wave 3 does not by itself activate Wave 4, Wave 5, Wave 6 or any other bounded scope.

## Producer / storage interpretation

The former producer-side narrative was too linear. Capacity observation, application work, historical representation work, backup design and other independent capabilities may advance in parallel when their declared dependencies are met.

Backfill/repair does not require the entire storage-tiering program. Backup/restore must precede deletion authority but need not wait for tier relocation. L1/L2 contract work requires real feed evidence, not a second venue. API transport does not precede the completed in-process ASS-02 service.

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
