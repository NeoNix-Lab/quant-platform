# Quant Platform Roadmap

This document is the **macro product-progression view**. The authoritative atomic dependency/execution model is [`CAPABILITY_DAG.md`](CAPABILITY_DAG.md). Exact semantic authority remains in accepted ADRs/contracts; `SCOPE.md` activates bounded work.

## Planning layers

```text
ROADMAP.md          = product progression
CAPABILITY_DAG.md   = atomic capability dependencies + decision gates + verticals
CAPABILITY_MAP.md   = compact current-state snapshot
OPEN_DECISIONS.md   = unresolved decisions / gate triggers
ADRs + contracts    = semantic authority
SCOPE.md            = active bounded authorization
```

A numbered phase is not an executable work packet. Parallel work is allowed when the Capability DAG dependencies are satisfied.

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
Broad independent Producer/Consumer expansion        UNLOCKED
```

The Conformity Gate pair is concluded. Package Boundary established the modular-monolith ownership/dependency rules. Legacy Harvest classified 31 capabilities and retained H01 as the first **legacy harvest candidate** without activating it. ASS-01 established `quant_platform.application` as the in-process composition owner without adding application use-case runtime, API transport, Job runtime or clients.

Completed checkpoints are credited and must not be re-proved absent a concrete invalidating change.

## Roadmap vNext planning state

The architecture-roadmap atomization audit classified the full platform planning inventory:

```text
TOTAL_ATOMS                     87
CLASSIFIED_ATOMS                87
UNCLASSIFIED_GAPS               0
SEMANTIC_FROZEN_OR_RESOLVED     49 / 87 = 56.3%
OPEN_BLOCKING                   30
OPEN_DEFERABLE                  8
ROADMAP_PLANNING_COMPLETENESS   >= 90%
```

The 56.3% value is a semantic-freeze metric, not roadmap readiness. Future choices are not frozen merely to increase a percentage. A deferable decision is roadmap-defined when its trigger and required evidence are explicit.

See [`CAPABILITY_DAG.md`](CAPABILITY_DAG.md) for all 87 atoms and their dependencies.

## Decision-gate model

Open blockers are grouped into activation boundaries:

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

These are not eight mandatory design projects to run immediately. Each gate is activated only when a selected dependent atom reaches it.

Explicitly deferable choices — second-provider resolution, exact L3/MBO semantics, DatasetSnapshot shape, future schema evolution, generic provider extension, multi-asset execution and concrete API transport — remain open until their evidence trigger exists.

## Vertical milestones

The cross-spine integration model is:

```text
V1  Source -> canonical -> catalog -> DataGateway         COMPLETE
V2  DataGateway -> Application service                   READY_CHAIN
V3  DataGateway -> historical Candle                     READY
V4  Representation -> Feature -> canonical H01           BLOCKED by DG-A
V5  Feature -> Research                                  BLOCKED by FeatureArtifact
V6  Research -> Validation                               BLOCKED by DG-E
V7  Strategy -> deterministic Replay                     BLOCKED by DG-F
V8  Historical -> Live                                   BLOCKED by DG-B/DG-H
V9  Application -> API -> Client                         application-ready; transport deferred
V10 Paper/Shadow -> Live                                 BLOCKED by runtime/operational gates
```

V1 is the accepted Bybit BTCUSDT first vertical. Its detailed evidence remains in the existing conformity/integration documentation.

## Current execution frontier

The following atoms are decision-complete, missing, and have their declared planning dependencies satisfied:

```text
C02  ASS-02 semantic selector resolution
D03  historical Candle runtime (on-demand only)
E05  H01 pure imbalance kernel
F05  deterministic walk-forward schedule
I01  Study/Trial/Run/Artifact semantic model
K04  observational capacity monitoring
K11  governance-state consistency
```

This frontier is **not authorization** and no next implementation atom is selected here. `SCOPE.md` must activate one bounded slice.

## Execution waves

Waves are planning groupings, not new numbered phases:

```text
Wave 0  Architecture foundation                           COMPLETE
Wave 1  First canonical computation verticals             current frontier
Wave 2  Representation / Feature vertical                 DG-A then dependent atoms
Wave 3  Research / Validation                             research foundation then DG-E
Wave 4  Strategy / Replay                                 strategy foundation then DG-F
Wave 5  Experiment / Supervised ML                       experiment identity/persistence then ML
Wave 6  Live Data Plane                                   K04 + required DG-H, then DG-B/live
Wave 7  Runtime / Clients                                 application service -> transport -> clients
Wave 8  Paper / Live product                             paper/shadow then explicit live gate
```

Parallelism is expected where DAG dependencies permit it. In particular, ASS-02, historical Candle, pure H01, deterministic walk-forward semantics, experiment identity and observational capacity do not depend on one another merely because the macro phases are numbered.

## Producer/Data Plane path

The first historical publication/read vertical is complete:

```text
canonical trade semantics
 -> materialization/manifests/coverage
 -> S13 certification
 -> S14 publication
 -> catalog
 -> bounded DataGateway read
 -> Human Golden exact match
```

Future Data Plane work is no longer represented as one false linear chain. Important independent branches include:

```text
K04 capacity observation -> DG-H pressure/protection/restore/retention as needed
DG-B coverage/repair/live convergence -> A10/A11/B06
DG-C -> L1/L2 only when a concrete feed triggers it
second venue only when real second-provider evidence exists
L3/MBO only when a real L3 feed exists
```

Backfill/repair does not require every storage-lifecycle capability to be complete. API/application work and observational operations work may proceed independently of the live path.

## Application Service Seam

```text
ASS-01 ownership + architecture enforcement         COMPLETE
C02/C03 ASS-02 first application vertical           NOT ACTIVE
C04/C05 ASS-03 orchestration/config convergence     NOT ACTIVE
```

ASS-02 can remain entirely in-process and does not require phase 9 transport. ASS-03 requires DG-D configuration resolution and a real ASS-02 service target before historical orchestration debt is migrated.

## Representation / Feature / H01

CandleDefinition v1 semantics are accepted. Historical on-demand candle computation (`D03`) is on the current frontier; candle persistence identity (`D05`) remains gated by DG-A.

H01 remains split deliberately:

```text
E05 pure imbalance kernel          decision-complete, implementation missing
E06 canonical H01 integration      blocked by D06/E02/E04 and DG-A
```

Do not treat the pure kernel as a complete Feature/Footprint vertical.

## Runtime and clients

Consumer API semantics are already frozen. Concrete API transport is deliberately deferable until a real remote/client need exists. Long-running durable work requires the separate Job decision/runtime atom. Clients remain thin and cannot own quantitative or storage logic.

## Operations

Operational capability is cross-cutting and progressively gated. `K04` observational capacity monitoring can proceed independently. Destructive/shared-state capabilities require their own operational decision gate and authorization.

Important internal ordering includes:

```text
K04 -> K05 pressure policy
K06 source protection -> K08 restore proof -> K09 deletion authority
K03 observability + K08 restore proof -> K10 recovery
```

Server/runtime identity hardening remains a separate operational concern and does not authorize an interim API/runtime.

## Roadmap rule

Before implementation:

1. verify current `origin/main`;
2. select one atom from the DAG, not a whole phase;
3. verify its dependencies;
4. activate only its required decision gate(s);
5. credit existing evidence;
6. define the exact missing proposition and minimum proof;
7. authorize mutation through a bounded scope/branch.

`REFERENCE > REPETITION`, `CREDIT > RE-PROVE`, and observed need remains preferable to speculative future flexibility.
