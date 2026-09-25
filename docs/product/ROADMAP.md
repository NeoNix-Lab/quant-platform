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

A numbered phase is not an executable work packet. Parallel work is allowed when Capability DAG dependencies are satisfied, but the active scope may impose a stricter one-slice-at-a-time policy.

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
E06 H01 canonical integration                        COMPLETE
F02 EventSpec/detection                              COMPLETE
F03 OutcomeSpec/Outcome                              COMPLETE
F04 Event studies and parameter sweep runtime v1     COMPLETE
F07 labels/censoring/lockbox                         COMPLETE
F08 DSR/PBO robust comparison                        COMPLETE
K08 backup / restore v1                              COMPLETE
A11 Bybit live trades acquisition v1                 COMPLETE
K02 live-ingest runtime identity v1                  COMPLETE
K10 checkpoint / recovery v1                        COMPLETE (real restart proof: PR #122)
Wave 1 implementation batch                         CONCLUDED
Wave 2 implementation batch                         COMPLETE
Wave 3 implementation batch                         COMPLETE
Broad independent Producer/Consumer expansion        UNLOCKED
```

Wave 3 is implementation-complete through F08. The selected **Live Ingest Vertical v1** authority set remains:

```text
ADR-0039  K08 backup / restore v1
ADR-0040  A11 Bybit live trades acquisition v1
ADR-0041  K02 live-ingest runtime identity v1
ADR-0042  K10 checkpoint / recovery v1
```

PR #116 has integrated the Live Ingest implementation branch into `main`. K08, A11, K02 and K10 are now implementation `COMPLETE`: PR #122 (issues #109, #121) supplies K10's previously pending bounded real-server restart/deployed-checkpoint proof.

Completed checkpoints are credited and must not be re-proved absent a concrete invalidating change.

## Roadmap vNext planning state

```text
TOTAL_ATOMS                     87
CLASSIFIED_ATOMS                87
UNCLASSIFIED_GAPS               0
SEMANTIC_FROZEN_OR_RESOLVED     68 / 87 = 78.2%
OPEN_BLOCKING                   11
OPEN_DEFERABLE                  8
ROADMAP_DEFINED                 68 + 11 + 8 = 87
ROADMAP_PLANNING_COMPLETENESS   87 / 87 = 100%
IMPLEMENTATION_COMPLETE         49 / 87 = 56.3%
```

The 100% planning score means every roadmap atom is classified. It does **not** mean every future semantic choice is frozen.

The selected Live Ingest scope additionally carries one scope-level open DG-B proposition that is not a new atom and therefore is not included in these counts: remediation of a live gap that exceeds the provider's bounded reconciliation window.

## Decision-gate model

```text
DG-A  Representation / Feature integration
DG-B  Historical / Live data convergence
DG-C  Market-data depth (L1/L2)
DG-D  Application configuration / ASS-03      RESOLVED
DG-E  Validation semantics                   RESOLVED / IMPLEMENTED
DG-F  Strategy / Execution semantics
DG-G  Experiment / RL / Jobs
DG-H  Operational safety
```

Important current boundaries:

- DG-A: `D05` remains independent from completed H01.
- DG-B: A10/B04 historical repair is complete; A11 is frozen and implemented; B06 consumer-live cursor remains open/outside the selected scope.
- DG-B long-gap remediation remains `OPEN_BLOCKING` for claiming an unreconciled interruption filled/lossless. A11/K10 may still record an explicit gap and continue with a new governed segment.
- DG-C: L1 does not activate L2; L3 waits for real feed evidence.
- DG-E: complete through F08.
- DG-H: K02/K03/K04/K05/K06/K08/K10 are complete, including K10's real restart proof (PR #122); K07/K09 remain open and are outside current scope.

Explicit deferables — second-provider resolution, exact L3/MBO semantics, DatasetSnapshot shape, future schema evolution, generic provider extension, multi-asset execution and concrete API transport — remain open until their evidence trigger exists.

## Operational dependency direction

```text
K06 source protection -> K08 backup/restore proof -> A11 live acquisition
A11 live acquisition -> K10 checkpoint/recovery implementation
K06 source protection -> K07 relocation
K08 backup/restore proof -> K09 deletion authority
```

K08/A11/K02/K10 implementation and real-server evidence are now credited, including K10's real restart proof (PR #122). Backup/restore does not require tier relocation.

## Vertical milestones

```text
V1  Source -> canonical -> catalog -> DataGateway         COMPLETE
V2  DataGateway -> Application service                   COMPLETE
V3  DataGateway -> historical Candle                     COMPLETE
V4  Representation -> Feature -> canonical H01           COMPLETE
V5  Feature -> Research                                  COMPLETE
V6  Research -> Validation                               COMPLETE
V7  Strategy -> deterministic Replay                     BLOCKED by DG-F / upstream implementation
V8  Historical -> Live                                   PARTIAL; first live-ingest path integrated including K10's real-restart proof, production-readiness now the active scope
V9  Application -> API -> Client                         application service complete; transport deferred
V10 Paper/Shadow -> Live                                 BLOCKED by runtime/operational gates
```

V8 is not complete. The first live-ingest producer path now converges with canonical publication/storage/access under K08/A11/K02/K10 evidence, including K10's real-server restart proof (PR #122); B06 remains outside the selected first-ingest scope; and any long gap beyond bounded provider reconciliation must remain explicit until authoritative repair evidence exists.

## Current execution frontier

PR #116 integrated `implement/live-ingest` into `main`, and PR #122 closed K10's previously pending real-server restart/deployed-checkpoint proof. The `Live Ingest Vertical v1` macro-scope's sole acceptance blocker is therefore resolved. That scope is superseded by **Live Ingest Server Production Readiness v1**, governed by `SCOPE.md`.

The current bounded frontier is the production-readiness path defined there: a persistent bounded ingest-server loop under K02, a governed long-gap detection/repair-orchestration path built on the disposed DG-B finding (#110), and operator/runbook evidence — followed by a real-server production-readiness proof and governance closeout.

Long-gap remediation is not a speculative implementation task. It activates only when a qualifying unresolved gap/source proposition exists. Until an attributable repair source/path proves the missing interval, that interval remains explicit non-complete coverage. DG-B's disposition (#110, `NO_AUTHORITATIVE_REPAIR_PATH_PROVEN`) stands; production readiness does not reopen it.

B06 live-consumer access remains outside the selected macro-scope.

Frontier/readiness state is **not concurrent authorization**; `SCOPE.md` still allows one bounded mutation slice at a time.

## Execution waves

Waves are dependency/value groupings, not a new linear phase numbering, and are distinct from temporary `implement/wave-*` integration branches.

```text
Wave 0  Architecture foundation                           COMPLETE
Wave 1  First canonical computation/application slices    COMPLETE
Wave 2  Representation / Feature                          COMPLETE
Wave 3  Research / Validation                             COMPLETE
Wave 4  Strategy / Replay                                 G01-G03 then DG-F/H01-H05
Wave 5  Experiment / Supervised ML                       I01,I02 COMPLETE; I03-I05 remain
Wave 6  Live Data Plane                                   K02,K03,K04,K05,K06,K08,K10,A10,A11,A16 COMPLETE; K07,K09,B06 remain open/missing
Wave 7  Runtime / Clients                                 J02 -> thin clients when real client need exists
Wave 8  Paper / Live product                             J07 -> J08 after required data/execution/ops gates
```

The selected Live Ingest scope is a bounded path through part of Wave 6, not authorization to implement Wave 6 generally.

## Producer / storage interpretation

Capacity observation, application work, historical representation work, backup design and other independent capabilities may advance in parallel when their declared dependencies are met, subject to the active scope's mutation policy.

Backfill/repair does not require the entire storage-tiering program. K08 backup/restore and A11 live acquisition are complete for the selected first-live path; K09 deletion authority remains separate, and tier relocation remains independent. API transport does not precede the completed in-process ASS-02 service.

## Planning rule

Before opening implementation:

1. verify the current authoritative branch/ref;
2. read `SCOPE.md` and the selected atom's accepted ADR;
3. verify its transitive `Requires` path;
4. credit existing evidence;
5. define only the exact missing implementation/proof proposition;
6. keep unrelated frontier atoms inactive;
7. reconcile governance only after integrated evidence changes state.

Roadmap state never authorizes production/runtime mutation by itself.
