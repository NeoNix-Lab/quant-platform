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
Live Ingest Server Production Readiness v1          COMPLETE for first producer operating path
Wave 4 Strategy / Replay                            COMPLETE (G01-G04,H01-H05; V7 Golden PASS)
Wave 6 Live Consumer Data Plane & Storage Lifecycle COMPLETE (B06,D04,K07,K09; Golden PASS)
Wave 7 API & Platform Transport v1              COMPLETE (J02,J04,J05,J06; exceptional Golden waiver)
Omega external-audit stabilization line         COMPLETE (not a wave; tracking #232; ADR-0054-0061 and
(implement/omega, PR #254,#256-#278)                ADR-0037/ADR-0050 amendments; see Decision-gate model)
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
TOTAL_ATOMS                     96
CLASSIFIED_ATOMS                96
UNCLASSIFIED_GAPS               0
SEMANTIC_FROZEN_OR_RESOLVED     75 / 96 = 78.1%
OPEN_BLOCKING                   14
OPEN_DEFERABLE                  7
ROADMAP_DEFINED                 75 + 14 + 7 = 96
ROADMAP_PLANNING_COMPLETENESS   96 / 96 = 100%
IMPLEMENTATION_COMPLETE         69 / 96 = 71.9%
```

Recomputed mechanically from `CAPABILITY_DAG.md`'s atom tables after the Omega
stabilization line and the Wave 8 projection: nine new atoms (`J09`-`J15`,
`K12`, `K13`) entered at `OPEN_BLOCKING`/`MISSING` for Wave 8's design gates,
and `D05` moved from `OPEN_BLOCKING` to `FROZEN` under ADR-0059 (implementation
unchanged, still `MISSING`). The semantic-freeze percentage dropping from
85.1% to 78.1% is nine real future decisions becoming visible, not planning
regression. The 100% planning score means every roadmap atom is classified. It does **not** mean every future semantic choice is frozen.

The closed Live Ingest Server Production Readiness v1 scope also carried one scope-level DG-B proposition that is not a new atom and therefore is not included in these counts: remediation of a live gap that exceeds the provider's bounded reconciliation window. The accepted disposition remains `NO_AUTHORITATIVE_REPAIR_PATH_PROVEN`: the server may record the explicit non-complete gap and continue with a new governed live segment, but may not claim the missing interval filled/lossless without future attributable repair evidence.

## Decision-gate model

```text
DG-A  Representation / Feature integration
DG-B  Historical / Live data convergence
DG-C  Market-data depth (L1/L2)
DG-D  Application configuration / ASS-03      RESOLVED
DG-E  Validation semantics                   RESOLVED / IMPLEMENTED
DG-F  Strategy / Execution semantics                RESOLVED / IMPLEMENTED
DG-G  Experiment / RL / Jobs
DG-H  Operational safety
DG-I  API transport / client convergence        RESOLVED / IMPLEMENTED
DG-J  Remote Service Topology (Wave 8)           PROJECTED / design gates G1-G6, not authorized
```

Important current boundaries:

- DG-A: `D05`'s semantics are frozen under ADR-0059 (implementation still `MISSING`); it remains independent from completed H01.
- DG-B: A10/B04 historical repair is complete; A11 is frozen and implemented; B06 consumer-live cursor is frozen and implemented by Wave 6.
- DG-B long-gap remediation remains blocking only for claiming an unreconciled interruption filled/lossless. ADR-0044 closes the selected production-readiness path by retaining explicit-gap-only behavior until a future authoritative repair source/path is proven.
- DG-C: L1 does not activate L2; L3 waits for real feed evidence.
- DG-E: complete through F08.
- DG-F: G04 session/cooldown semantics are frozen by ADR-0045; H03 execution-conflict semantics are frozen by ADR-0046; G01-G04/H01-H05 are implementation-complete on `implement/wave-4`.
- DG-G: the supervised branch I03/I04/I05 is implementation-complete and Golden-proven by Wave 5; remaining unresolved branches are I06 strategic RL, I07 execution RL and J03 job runtime.
- DG-H: K02/K03/K04/K05/K06/K07/K08/K09/K10 are complete, including K10's real restart proof (PR #122), the supervised live-ingest server operating path proof, and the Wave 6 storage lifecycle proof.

Explicit deferables — second-provider resolution, exact L3/MBO semantics, DatasetSnapshot shape, future schema evolution, generic provider extension and multi-asset execution — remain open until their evidence trigger exists. Concrete API transport was resolved by ADR-0050 in Wave 7, except authentication/TLS for non-loopback deployment, which the new DG-J gate G1 covers.

- DG-J: new family for the Wave 8 projection; see `docs/architecture/OPEN_DECISIONS.md` for the full G1-G6 text. Not authorized; each gate needs its own ADR before the atom(s) it blocks may be implemented.

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
V7  Strategy -> deterministic Replay                     COMPLETE
V8  Historical -> Live                                   COMPLETE for the bounded data-plane/storage lifecycle path; broader live product gates remain open
V9  Application -> API -> Client                         COMPLETE for bounded WebSocket transport and thin clients
V10 Paper/Shadow -> Live                                 BLOCKED by runtime/operational gates
```

V8 is complete for the bounded data-plane/storage lifecycle path selected through Wave 6: the first live-ingest producer path and the live consumer/candle/storage lifecycle path now converge through K08/A11/K02/K10, B06/D04 and K07/K09 evidence, ADR-0043/ADR-0044/ADR-0047/ADR-0048/ADR-0049, and the Wave 6 Golden proof. This does not complete J07/J08, broker/live execution, clients, second venue or market-depth branches; any long gap beyond bounded provider reconciliation must remain explicit until authoritative repair evidence exists.

## Current execution frontier

Wave 7 API & Platform Transport is closed on `implement/wave-7`: ADR-0050 and
PR #225/#226/#227/#228 complete J02/J04/J05/J06. Issue #222 was closed by
explicit operator exception during expedited closeout, so this roadmap state
does not claim an additional dedicated Golden E2E artifact.

The post-Wave-7 `implement/omega` stabilization line (tracking #232) is
reconciled: eight design gates (ADR-0054-0061) and nine implementation/
hardening slices (PR #254, #256-#278), plus amendments to ADR-0037 and
ADR-0050. It resolves `D05`'s semantics (implementation stays `MISSING`) and
closes the audited findings; it is not a wave and selects no new runtime/
product frontier by itself.

`SCOPE.md`'s "Wave 8 — Full Platform API & Remote Service Topology v1"
projects the next bounded scope as `DG-J` design gates G1-G6. This is a
priority/projection, not authorization: no implementation issue may open
against it until its specific gate's ADR is accepted.

No new runtime/product frontier is authorized by this closeout or the
stabilization line. H06, I06-I07, J07/J08, RL, live broker execution, second
venue and L1/L2/L3 market depth remain outside all of them. J07 remains
`MISSING`.

Roadmap state is still **not concurrent authorization**.

## Execution waves

Waves are dependency/value groupings, not a new linear phase numbering, and are distinct from temporary `implement/wave-*` integration branches.

```text
Wave 0  Architecture foundation                           COMPLETE
Wave 1  First canonical computation/application slices    COMPLETE
Wave 2  Representation / Feature                          COMPLETE
Wave 3  Research / Validation                             COMPLETE
Wave 4  Strategy / Replay                                 COMPLETE
Wave 5  Experiment / Supervised ML                       COMPLETE (I01-I05; Golden supervised E2E PASS)
Wave 6  Live Data Plane                                   COMPLETE (B06,D04,K07,K09 plus credited K02,K03,K04,K05,K06,K08,K10,A10,A11,A16; Golden PASS)
Wave 7  Runtime / Clients                                 COMPLETE (J02 -> J04/J05/J06; exceptional Golden waiver)
Wave 8  Full Platform API & Remote Service Topology v1    OPEN -- design gates first (DG-J G1-G6). Strategy/
                                                           Replay/Validation/Training exposed through Consumer-
                                                           API-equivalent seams carried over J02 (G4);
                                                           J04/J05/J06 extended to reach them; plus the
                                                           remote-topology pieces found necessary while
                                                           integrating the first real client (Omega): J03 job
                                                           runtime (G2), auth/TLS for non-loopback J02 (G1),
                                                           the data-local/consumer-local placement contract and
                                                           admitted-input/result handoff (G3), transport
                                                           evolution for larger/streamed results (G5), and the
                                                           Omega client contract itself (G6). See SCOPE.md.
Wave 9  Reinforcement Learning                            I06 strategic RL, I07 execution RL; harvest-audit
                                                           evidence sources: ml_core (already the Wave 5 legacy
                                                           baseline), JJJerome/mbt_gym, sadighian/crypto-rl
Wave 10 Paper / Live product                              J07 -> J08 after required data/execution/ops gates;
                                                           implementation intends to draw architectural
                                                           inspiration from nautilus_trader as read-only
                                                           reference evidence only, never a runtime dependency
```

The earlier closed Live Ingest scope was a bounded path through part of Wave 6, not authorization to implement Wave 6 generally. Wave 6 itself is now complete only for the bounded live data-plane/storage lifecycle path described above.

### Post-Wave-7 sequencing: "platform v1.0"

Waves 0-7 are the accepted foundation: canonical data plane, representation,
research/validation, strategy/replay, supervised ML, live consumer data plane
and storage lifecycle, and now API transport with thin clients. The operator
has set an explicit priority order for what comes next, driven by usability
of what is already built rather than by new capability breadth:

1. **Wave 8 first** — the already-complete Strategy/Replay/Validation/
   Training engines (Waves 2-5) are only reachable today by writing a Python
   script against the domain packages directly; there is no Consumer API or
   client path for any of them. Wave 8 closes that gap the same way Wave 7
   closed it for market data: a `C02`/`C03`-equivalent seam per domain,
   carried over the existing J02 transport, reachable from J04/J05/J06.
2. **Wave 9 second** — `I06`/`I07` remain `OPEN_BLOCKING` under `DG-G` and are
   independent of Waves 8 and 10 in the capability DAG; a legacy-harvest audit
   against `ml_core`, `JJJerome/mbt_gym` and `sadighian/crypto-rl` precedes any
   implementation issue, following the same disposition discipline
   `ADOPTION_LEDGER.md` already established for Wave 5's supervised-ML harvest.
3. **Wave 10 last** — `J07`'s own DAG dependencies (`A11`, `H05`, `I05`, `J02`,
   `K03`) are already satisfied, so it could technically start earlier; it is
   deliberately sequenced last because it delivers a new capability rather
   than unlocking use of ones already built. `nautilus_trader` is noted here
   as the intended architectural reference for this wave's eventual design
   gate, under the same read-only-evidence discipline as every other
   third-party/legacy reference this repository has used.

This is a priority order, not authorization: each wave still requires its own
`SCOPE.md` and, where a decision remains open, its own design gate before any
implementation issue may start.

## Producer / storage interpretation

Capacity observation, application work, historical representation work, backup design and other independent capabilities may advance in parallel when their declared dependencies are met, subject to the active scope's mutation policy.

Backfill/repair does not require the entire storage-tiering program. K08 backup/restore, A11 live acquisition, B06 live consumer access, D04 live candles, K07 tier relocation and K09 deletion authority are complete for the selected bounded Wave 6 path. API transport does not precede the completed in-process ASS-02 service.

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
