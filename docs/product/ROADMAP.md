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
DG-J Remote Service Topology design gates       RESOLVED (issues #282-#287/#288; ADR-0062-0067; G1-G6;
(implement/omega, PR #289-#294)                     J11 deferred; Wave 8 implementation inventory authorized)
Wave 8 Full Platform API & Remote Service       COMPLETE (J03,J09,J10,J12-J15,K12,K13; operator closeout #327;
Topology v1 (PR #322, #326)                         J10/J12/J13 in-process only; no Golden proof claimed)
DG-K Process Topology v1                         COMPLETE within recorded bounds (operator closeout #381; ADR-0068,
                                                    ADR-0069; P01-P06 promoted into main via #434/#437;
                                                    later composition credited by DG-K2, no Golden proof claimed)
DG-K2 Server/deck deployment composition        COMPLETE within recorded bounds (operator closeout #409; ADR-0072 and Amendment 1;
                                                    S01-S07 on implement/dgk2; supervised proof: one happy path; S08 not authorized)
DG-C L2 historical v1 (implement/l2, #442)       COMPLETE (A14,A14a-A14e; PR #375/#379/#401/#411/#417/#408;
                                                    golden #413/#430, 11/11 at 1cdd78f; explicit gaps);
                                                    A14f MISSING (gate released #448), A14g held MISSING;
                                                    promotion to main is separate
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
TOTAL_ATOMS                     = 119
CLASSIFIED_ATOMS                = 119
UNCLASSIFIED_GAPS               = 0
SEMANTIC_FROZEN_OR_RESOLVED     = 107 / 119 = 89.9%
OPEN_BLOCKING                   = 3
OPEN_DEFERABLE                  = 9
ROADMAP_DEFINED                 = 107 + 3 + 9 = 119
ROADMAP_PLANNING_COMPLETENESS   = 119 / 119 = 100%
IMPLEMENTATION_COMPLETE         = 99 / 119 = 83.2%
```

Reconciliation #444 imports main at `693d765` into the L2 closeout line,
preserving both inventories and credits. The union is 119 atoms / 99 COMPLETE;
[the executable verifier](../../SCOPE.md#executable-governance-verification)
checks both parents and all four current inventory blocks. L2 v1 (#442/#443)
credits A14 and A14a-A14e with golden #413/#430 (11/11); A14g stays held and A14f's start gate was
released by #448 (MISSING).

Earlier, DG-K2 closeout (#409): `S01`-`S07` move from `MISSING` to `COMPLETE` (86 -> 93 of 112), each within the bound recorded in its row;
no decision state changes (semantic stays 88.4%).

Earlier, DG-K2 reconciliation (#389): ADR-0072 is `ACCEPTED`; `S01`-`S07` move from `OPEN_BLOCKING` to `FROZEN` (semantic 82.1% -> 88.4%;
`OPEN_BLOCKING` 11 -> 4); their implementation state stays `MISSING` (86 / 112 = 76.8% unchanged).

Earlier, DG-K2 registration (#382): eight candidate atoms `S01`-`S08` (`OPEN_BLOCKING` except the deferable `S08`) are added,
all `MISSING`. No existing atom changes state; the percentages fall because the inventory grows from 104 to 112
atoms (semantic 88.5% -> 82.1%; implementation 82.7% -> 76.8%), not because anything regressed.

Earlier, DG-K closeout (#381): `P01`, `P02`, `P03`, `P04a`, `P04b`, `P05a`, `P05b` and `P06` move from
`MISSING` to `COMPLETE` (78 -> 86), each within the bound recorded in
`CAPABILITY_DAG.md`; no decision state changes (semantic score stays 88.5%).

Earlier, DG-K design-gate reconciliation (#319): ADR-0068 and ADR-0069 are accepted,
so `P01`-`P06` moved from `OPEN_BLOCKING` to `FROZEN` (84 -> 92 decided);
implementation stayed `MISSING` until the closeout above.

Earlier, DG-K registration (issue #318) added eight candidate atoms (`P01`, `P02`,
`P03`, `P04a`, `P04b`, `P05a`, `P05b`, `P06`) as `OPEN_BLOCKING`/`MISSING`. No
existing atom changes state; the percentages fall because the inventory grows
from 96 to 104 atoms, not because anything regressed.

Earlier, the Wave 8 closeout (issue #327) moves `J03`, `J09`, `J10`, `J12`, `J13`, `J14`,
`J15`, `K12` and `K13` from `MISSING` to `COMPLETE` (69 -> 78), each within the
bound recorded in `CAPABILITY_DAG.md`; no decision state changes.

Earlier, recomputed mechanically from `CAPABILITY_DAG.md`'s atom tables after the
`DG-J` design-gate reconciliation (issue #288): all six gates reached a
disposition, moving `J03`, `J09`, `J10`, `J12`, `J13`, `J14`, `J15`, `K12` and
`K13` from `OPEN_BLOCKING` to `FROZEN` under ADR-0062 through ADR-0067, and
`J11` from `OPEN_BLOCKING` to `OPEN_DEFERABLE` under ADR-0065 §5's explicit
deferral. `IMPLEMENTATION_COMPLETE` is unchanged at 71.9% — none of these nine
atoms is implemented. The 100% planning score means every roadmap atom is
classified. It does **not** mean every future semantic choice is frozen.

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
DG-J  Remote Service Topology (Wave 8)           RESOLVED / IMPLEMENTED
DG-K  Process Topology v1                        RESOLVED / IMPLEMENTED within recorded bounds (ADR-0068, ADR-0069; closeout #381)
DG-K2 Server/deck deployment composition         RESOLVED / IMPLEMENTED within recorded bounds (ADR-0072, Amendment 1; closeout #409)
```

Important current boundaries:

- DG-A: `D05`'s semantics are frozen under ADR-0059 (implementation still `MISSING`); it remains independent from completed H01.
- DG-B: A10/B04 historical repair is complete; A11 is frozen and implemented; B06 consumer-live cursor is frozen and implemented by Wave 6.
- DG-B long-gap remediation remains blocking only for claiming an unreconciled interruption filled/lossless. ADR-0044 closes the selected production-readiness path by retaining explicit-gap-only behavior until a future authoritative repair source/path is proven.
- DG-C: L1 does not activate L2; L3 waits for real feed evidence. L2 v1 historical ingest plus consumer read is COMPLETE on `implement/l2` (#442), independent of `A13`, under ADR-0070 and Amendments 1 (#403) and 2 (#417). UTC-day ownership is by `exchange_ts`, congruent with `trade-v1`; explicit head/tail gaps remain. ADR-0049 Amendment 1 (#424) allows disposal of one redundant physical copy, never the last. #425/#426 is credited as an operational tool outside A14 and adds no K09/atom credit. [Eight triggers, none authorized](../architecture/OPEN_DECISIONS.md#l2-v1-reopening-triggers-442); `A14f`/`A14g` remain held.
- DG-E: complete through F08.
- DG-F: G04 session/cooldown semantics are frozen by ADR-0045; H03 execution-conflict semantics are frozen by ADR-0046; G01-G04/H01-H05 are implementation-complete on `implement/wave-4`.
- DG-G: the supervised branch I03/I04/I05 is implementation-complete and Golden-proven by Wave 5; J03's job-runtime decision is frozen under ADR-0062 and its durable record is implemented (Wave 8; dispatch/worker belong to DG-K); remaining unresolved branches are I06 strategic RL and I07 execution RL.
- DG-H: K02/K03/K04/K05/K06/K07/K08/K09/K10 are complete, including K10's real restart proof (PR #122), the supervised live-ingest server operating path proof, and the Wave 6 storage lifecycle proof.

Explicit deferables — second-provider resolution, exact L3/MBO semantics, DatasetSnapshot shape, future schema evolution, generic provider extension, multi-asset execution, and the Replay Consumer-API seam (`J11`, not planned per owner decision 2026-10-10: replay runs on the deck over K12/K13) — remain open until their evidence trigger exists. Concrete API transport was resolved by ADR-0050 in Wave 7; authentication/TLS for non-loopback deployment is frozen under ADR-0063 and implemented by `J09` (Wave 8).

- DG-J: all six gates resolved (ADR-0062 through ADR-0067); see `docs/architecture/OPEN_DECISIONS.md` for the full G1-G6 disposition text. `J03`, `J09`, `J10`, `J12`-`J15`, `K12`, `K13` are frozen and implemented (Wave 8 closeout #327, within recorded bounds); `J11` remains `OPEN_DEFERABLE`.
- DG-K: Process Topology v1 is resolved (#318 registration, #319 design gate: ADR-0068 process topology v1, ADR-0069 J02 carriage of consumer seams and deck handoff v1). `P01`-`P06` are `FROZEN`/`COMPLETE` within recorded bounds (operator closeout #381), merged into `implement/dgk`, not composed in any executable and without a Golden proof; owner inputs fix identity-preserving canonical profiles, hosts in `tools/`, one J03 worker, a free server with data-local J03 handlers only, heavy compute on the deck on its own initiative, and `J11` not planned. See `OPEN_DECISIONS.md` (DG-K).
- DG-K2: registered by #382 after the DG-K closeout; its design gate #383 produced ADR-0072, accepted on 2026-10-16, and #389 authorizes `S01`-`S07` (K12 authority, K13 composition, API host with TLS/mTLS, credential and trust operations, deck workload module, deployment units, two-part topology proof) on the integration branch `implement/dgk2` (name proposed, to be confirmed by the maintainer). Closed by operator closeout #409: all seven are implemented on `implement/dgk2` within the bounds recorded in `CAPABILITY_DAG.md`; the split is deployable only within the bounds of ADR-0072 Amendment 1; Omega adoption is out of scope. Default proposed placement: before Wave 9, because the split is the precondition for any remote consumer; the order remains the maintainer's.

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

Wave 8 — Full Platform API & Remote Service Topology v1 is closed by operator
closeout (issue #327): `J03`, `J09`, `J10`, `J12`, `J13`, `J14`, `J15`, `K12`
and `K13` are implemented and promoted to `main` (PR #322, #326). `J10`/`J12`/
`J13` are credited as in-process application seams only: no J02 wire message or
remote scope carries them yet. No Wave 8 Golden E2E proof artifact is claimed.
Owner-accepted extras #313/#314 are on `main` through `implement/wave-8.1`.

DG-K P01-P06 (#381) and DG-K2 S01-S07 (#409) are COMPLETE within their
recorded bounds and promoted into main (#434/#437). DG-K2 supplies the later
executable composition, credentials/runbook and deck workload module; its real
proof remains one happy path on the smallest partition, within ADR-0072
Amendment 1 bounds and the recorded findings. S08 is not authorized.

L2 v1 is CLOSED on implement/l2 (#442/#443), A14 and A14a-A14e COMPLETE,
golden #413/#430 accepted 11/11. Reconciliation #444 combines both histories
before a separate final L2 wave promotion. A14g stays held; A14f's start gate was released by #448
(MISSING, line `implement/l2-live`). The
[recorded limits](../../SCOPE.md#not-claimed) and eight
[unauthorized triggers](../architecture/OPEN_DECISIONS.md#l2-v1-reopening-triggers-442)
remain: no live L2, ob500, population guarantee or gap-free UTC-midnight read.

No new runtime/product frontier is authorized by this closeout, the Wave 8
closeout or the stabilization line. H06, I06-I07, J07/J08, RL, live broker execution, second
venue, L1/L3 and L2 beyond the bounded historical v1 credit remain outside all of them. J07 remains
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
Wave 8  Full Platform API & Remote Service Topology v1    COMPLETE (J03,J09,J10,J12,J13,J14,J15,K12,K13 within
                                                           recorded bounds; J10/J12/J13 in-process only; J11
                                                           deferred; no Golden proof claimed; operator closeout
                                                           #327). J02 carriage of J10/J12/J13 moves to DG-K P05.
DG-K    Process Topology v1 (design-gate line, not a wave) COMPLETE within recorded bounds (operator closeout #381); before Wave 9
DG-K2   Server/deck deployment composition              COMPLETE within recorded bounds (operator closeout #409); deployable only within ADR-0072 Amendment 1 bounds
DG-C/L2 L2 historical v1 (#442)                         COMPLETE on implement/l2: A14,A14a-A14e; golden #413/#430 11/11;
                                                           A14f MISSING (gate released #448), A14g held MISSING;
                                                           separate L2 promotion to main
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
   closed it for market data: a `C02`/`C03`-equivalent seam per domain.
   Wave 8 delivered those seams in-process (J10/J12/J13); carrying them over
   J02 and making them reachable from J04/J05/J06 was reassigned by the owner
   on 2026-10-08 to DG-K Process Topology v1, atom P05 (#318).
   **DG-K before Wave 9** (default recorded by #318; the order remains the
   maintainer's): DG-K Process Topology v1 finishes what Wave 8 left in-process
   (J02 carriage of J10/J12/J13, J03 dispatch and one worker, a PostgreSQL
   runtime store, the K12/K13 deck handoff over J02). Heavy compute stays on
   the deck on its own initiative and the server stays data-local; no `J11`
   gate follows DG-K (owner decision 2026-10-10).
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
