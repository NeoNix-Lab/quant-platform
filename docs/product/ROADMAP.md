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
F03 OutcomeSpec/Outcome                              COMPLETE (issue #86/PR #90)
F04 Event studies and parameter sweep runtime v1     COMPLETE (issue #92/PR #94)
Wave 1 implementation batch (implement/wave-1)       CONCLUDED (2026-09-19)
Wave 2 implementation batch (implement/wave-2)       COMPLETE (E06,F02,F03; issue #87 reconciliation)
Broad independent Producer/Consumer expansion        UNLOCKED
```

The two-stage Producer–Consumer Conformity Gate is governed by [ADR-0023](../decisions/ADR-0023-producer-consumer-conformity-gate-v1.md): the **Contract Freeze Gate** and **Conformity Implementation Gate** remain PASSED. The gate pair is concluded and is referenced here as credited foundation evidence, not reopened work.

Package Boundary established modular-monolith ownership/dependency rules. Legacy Harvest classified 31 capabilities and retained H01 as the first **legacy harvest candidate** without activating it. ASS-01 established `quant_platform.application` as the in-process composition owner without adding application use-case runtime, API transport, Job runtime or clients. C02 established the frozen `trades@1` semantic selector-resolution seam through PR #30; C03 completed the frozen result/error translation seam through PR #41 after exact-head `Quant Platform integrity #91` PASS. Together C02 + C03 complete the current in-process ASS-02 Application vertical. C05 configuration convergence and C04/ASS-03 tool orchestration convergence are both complete (C04 via issue #40/PR #44, 2026-09-14). Roadmap vNext governance-state consistency remains a credited foundation capability after the post-C03 reconciliation. E02 FeatureDefinition v1 is now frozen and complete under ADR-0026, with its bounded runtime semantic model implemented under `quant_platform.features`. D06 FootprintDefinition v1 is frozen and complete under ADR-0027, with its bounded historical FINAL representation runtime implemented under `quant_platform.representation`. B04 non-contiguous coverage reads v1 is frozen and complete under ADR-0029, with explicit `ALLOW_PARTIAL` DataGateway semantics implemented under `quant_platform.access`. K06 RAW/source protection v1 is frozen and complete under ADR-0032 (attributable-evidence `SafetyRelevanceAssertion` pattern, `quant_platform.operations.protection`). A10 backfill/repair v1 is frozen and complete under ADR-0033 (deterministic repair-intent/candidate/atomic-cutover, `quant_platform.data.repair`), with two accepted known limitations tracked there. E04 FeatureArtifact v1 is frozen and complete under ADR-0034 (`SupportShape`, FINAL-only sealing, exact-Fraction numerical equivalence, `quant_platform.features.artifacts`). E06 H01 canonical integration semantics are frozen under ADR-0035: Diagonal and Stacked are independent direct-Footprint features, exact input/parameter/output contracts and observation-universe/provenance rules are fixed, and one `h01_imbalance@1` FeatureSet owns the first durable bundle. E06 runtime is complete (issue #83/PR #88). The `implement/wave-1` implementation batch (issues #51-#77) concluded 2026-09-19, including issue #73's Human Golden E2E closeout (1440-candle 1m D03, official `GOLDEN E2E: PASS`), which is closed and credited. The `implement/wave-2` implementation batch (issues #83, #85, #86) is complete: F02 EventSpec/detection (issue #85/PR #89) and F03 OutcomeSpec/Outcome (issue #86/PR #90) are both implemented alongside E06, reconciled by issue #87. On `implement/wave-3`, F04 Event studies and parameter sweep runtime v1 is implemented (issue #92/PR #94).

Completed checkpoints are credited and must not be re-proved absent a concrete invalidating change.

## Roadmap vNext planning state

The corrected architecture-roadmap inventory is:

```text
TOTAL_ATOMS                     87
CLASSIFIED_ATOMS                87
UNCLASSIFIED_GAPS               0
SEMANTIC_FROZEN_OR_RESOLVED     62 / 87 = 71.3%
OPEN_BLOCKING                   17
OPEN_DEFERABLE                  8
ROADMAP_DEFINED                 62 + 17 + 8 = 87
ROADMAP_PLANNING_COMPLETENESS   87 / 87 = 100%
IMPLEMENTATION_COMPLETE         43 / 87 = 49.4%
```

The 100% planning score means every roadmap atom is classified: frozen/resolved, assigned to an atom-specific blocking gate, or explicitly deferable with a real evidence trigger. It does **not** mean every future semantic choice is frozen.

The 71.3% value is the current semantic-freeze/resolution metric. Future provider, live, RL, transport and operational choices are not frozen merely to increase it.

## Decision-gate model

Open blockers are grouped into gate families, while resolved gates remain as historical references. Activation is **atom-path specific**:

```text
DG-A  Representation / Feature integration
DG-B  Historical / Live data convergence
DG-C  Market-data depth (L1/L2)
DG-D  Application configuration / ASS-03      RESOLVED for C05 semantics
DG-E  Validation semantics
DG-F  Strategy / Execution semantics
DG-G  Experiment / RL / Jobs
DG-H  Operational safety
```

Important non-monolithic boundaries:

- DG-A: Candle materialization `D05` is independent from canonical H01. `D06`, `E02`, `E04` and E05 are complete; E06 semantics are frozen under ADR-0035 and only bounded implementation remains.
- DG-B: B04 disjoint historical coverage reads, A16 and A10 repair are complete for the accepted first vertical; repair semantics do not automatically activate live cursor semantics.
- DG-C: L1 may be selected without activating L2.
- DG-D: C05 semantics and implementation are complete; C04/ASS-03 is also complete (issue #40/PR #44, 2026-09-14 -- mis-tracked as missing until this correction).
- DG-E: DSR/PBO `F08` does not block Strategy/ML paths that depend on `F07`.
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
V5  Feature -> Research                                  COMPLETE (F02/F03/F04; issues #85/#86/#92, PR #89/#90/#94)
V6  Research -> Validation                               BLOCKED by DG-E validation branch
V7  Strategy -> deterministic Replay                     BLOCKED by DG-F
V8  Historical -> Live                                   BLOCKED by DG-B live branch + relevant DG-H; DAG acyclic
V9  Application -> API -> Client                         application service complete; transport deferred
V10 Paper/Shadow -> Live                                 BLOCKED by runtime/operational gates
```

V1 is the accepted Bybit BTCUSDT first vertical; its detailed evidence remains in existing conformity/integration documentation. V2 is complete at the in-process semantic service boundary: C02 resolves the consumer selector and C03 returns the canonical result/error envelope. V2 completion does not imply C05, C04/ASS-03, API transport, jobs or clients.

## Current execution frontier

`F04` (Event studies and parameter sweep runtime v1) is **COMPLETE** (issue #92/PR #94).
All decision-complete Research atoms (`F01`, `F02`, `F03`, `F04`) are now `COMPLETE`.

```text
DG-E (F07 labels/censoring/lockbox, F08 DSR/PBO)
```

Beyond `F04`, the next blocking decision-gate work on the Research/Validation path is DG-E:
`F07` (labels/censoring/lockbox, `OPEN_BLOCKING`, dependencies `F03`+`F06` satisfied)
and `F08` (DSR/PBO, `OPEN_BLOCKING`, dependencies `F04`+`F05` satisfied). Neither
atom may proceed to implementation until its decision gate is resolved.

Frontier membership is **not implementation authorization** and does not select the next atom.

## Execution waves

Waves are dependency/value groupings, not a new linear phase numbering, and
are a different concept from the `implement/wave-1` git branch (issues
#51-#77), which cut across several of these waves at once and concluded
2026-09-19.

```text
Wave 0  Architecture foundation                           COMPLETE
Wave 1  First canonical computation/application slices    COMPLETE (C05,D03,E05,F05,I01,K04)
Wave 2  Representation / Feature                          COMPLETE (E04,E06; ADR-0034/ADR-0035)
Wave 3  Research / Validation                             F01,F02,F03,F04,F05,F06 COMPLETE; F07/F08 then DG-E validation path remain
Wave 4  Strategy / Replay                                 G01-G03 then DG-F/H01-H05
Wave 5  Experiment / Supervised ML                       I01,I02 COMPLETE; I03-I05 remain
Wave 6  Live Data Plane                                   K03,K04,K05,K06,A10,A16 COMPLETE; K02,K07-K10,A11,B06 remain
Wave 7  Runtime / Clients                                 J02 -> thin clients when real client need exists
Wave 8  Paper / Live product                             J07 -> J08 after required data/execution/ops gates
```

Parallelism is allowed whenever DAG dependencies are satisfied. F04 (issue #92/PR #94) is COMPLETE; the next frontier on the Research/Validation path is DG-E (F07 labels/censoring/lockbox and F08 DSR/PBO).

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
