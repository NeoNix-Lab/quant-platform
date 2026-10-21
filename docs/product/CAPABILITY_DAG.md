# Quant Platform Capability DAG vNext

Status: **CANONICAL — reconciled by #444. DG-C / A14 historical v1 is CLOSED on
`implement/l2` (#442 / PR #443): A14 and A14a-A14e COMPLETE, golden #413/#430
accepted 11/11 under ADR-0070 Amendments 1-2. A14f/A14g remain held. DG-K P01-P06
(#381) and DG-K2 S01-S07 (#409) are COMPLETE within their row bounds and already
on main (#434/#437). DG-K2 supplies the later executable composition and one
supervised happy-path proof on the smallest real partition; deployability carries
ADR-0072 Amendment 1 bounds and the recorded proof findings. S08 is not authorized,
J11 remains deferred, and no Wave 8 or DG-K/DG-K2 Golden proof is claimed.
L2 promotion to main is a separate final wave PR; no deployment is performed here. DG-M — Canonical feature identity for deck
results v1 is CLOSED on `implement/fid` (#475). DG-N — Multi-instrument Bybit linear trades v1 is registered and authorized (#483, ADR-0074 `ACCEPTED`): `T01` and `T04` are `COMPLETE` within their row bounds (PR #486, #488; ADR-0074 Amendment 1, #489), `T03` and `T02` are `FROZEN`/`MISSING`. DG-O — Live publication: continuous session and append-only slices v1 is registered and authorized (#507, ADR-0075 `ACCEPTED`): `O01`-`O03` are `FROZEN`/`MISSING`, nothing is implemented, and `T02` additionally requires `O03`. DG-M detail: `M01`, `M02`, `M04` and `M05` are COMPLETE within their row bounds (PR #466, #468,
#473, #474) under ADR-0073 and its Amendment 1, `M03` is superseded; the server now seals the identities of the definitions a deck
names and the deck builder must declare them. No real-host run is claimed, and promotion into `main` is the operator's separate PR.**

This document is the execution/dependency view of the Quant Platform roadmap. `ROADMAP.md` remains the human-readable macro progression; `CAPABILITY_MAP.md` remains the compact current-state view; accepted ADRs/contracts remain semantic authority.

## Planning model

Roadmap readiness is not the same thing as freezing every future semantic choice.

An atom is roadmap-defined when its observable outcome, owner, dependencies, unlocks, acceptance proposition and decision state are classified, and any unresolved decision is either assigned to an atom-specific blocking gate or has an explicit deferable evidence trigger.

Current audited inventory after the DG-O registration (#507),
recomputed mechanically from the atom tables, not by hand:

```text
TOTAL_ATOMS                     = 130
CLASSIFIED_ATOMS                = 130
UNCLASSIFIED_GAPS               = 0
SEMANTIC_FROZEN_OR_RESOLVED     = 118 / 130 = 90.8%
OPEN_BLOCKING                   = 3
OPEN_DEFERABLE                  = 9
ROADMAP_DEFINED                 = 118 + 3 + 9 = 130
ROADMAP_PLANNING_COMPLETENESS   = 130 / 130 = 100%
IMPLEMENTATION_COMPLETE         = 105 / 130 = 80.8%
```

Reconciliation #444 preserves all accepted DG-K/DG-K2 credits from main and
all L2 v1 credits from #442: main has 112 atoms / 93 COMPLETE, the L2 line
had 111 / 84, and their union has 119 / 99. No new atom or capability is
invented by this merge; the seven L2 subatoms and eight S atoms were already
registered on their respective lines. [SCOPE.md](../../SCOPE.md#executable-governance-verification)
contains the executable two-parent verification.

DG-O registration and authorization (issue #507): ADR-0075 is `ACCEPTED` (#504, 2026-10-21). Three atoms `O01`-`O03` are added directly as
`FROZEN`/`MISSING` (127 -> 130 atoms; 115 -> 118 decided); no existing atom changes state, and `T02` gains the requirement `O03` (its one-hour
`ETHUSDT` proof is the one of `O03`). `T03` and `T02` parts 1 to 5 are merged and are credited by the DG-N closeout, not here. The counts above
are recomputed from these tables by script.

Earlier, DG-N reconciliation (issue #489): `T01` and `T04` move from `MISSING` to `COMPLETE` on `implement/fid` (103 -> 105; implementation
81.1% -> 82.7%); no new atom and no decision-state change (115 decided, 3 `OPEN_BLOCKING`, 9 `OPEN_DEFERABLE`, 127 atoms). ADR-0074
Amendment 1 (accepted 2026-10-20) names the Bybit public trade archive as the historical source and supersedes the registration of
the existing Parquet. The counts above are recomputed from these tables by script. Promotion into `main` is the operator's separate PR.

Earlier, DG-N registration and authorization (issue #483): ADR-0074 is `ACCEPTED` (#377, 2026-10-19). Four atoms `T01`-`T04` are added directly as
`FROZEN`/`MISSING` (123 -> 127 atoms; 111 -> 115 decided); no existing atom changes state. The counts above are recomputed from these
tables by script.

Earlier, DG-M closeout (issue #475): `M01`, `M02`, `M04` and `M05` move from `MISSING` to `COMPLETE` on `implement/fid` (99 -> 103; implementation
80.5% -> 83.7%); no new atom and no decision-state change (111 decided, 3 `OPEN_BLOCKING`, 9 `OPEN_DEFERABLE`, 123 atoms). The counts above
are recomputed from these tables by script. Promotion into `main` is the operator's separate PR; the real-host run is operator evidence.

Earlier, DG-M reconciliation (issue #458): ADR-0073 is `ACCEPTED` (#457, 2026-10-18). `M01`, `M02`, `M04` and `M05` move from
`OPEN_BLOCKING` to `FROZEN` (107 -> 111 decided; 8 -> 3 `OPEN_BLOCKING`) and stay `MISSING`; `M03` is `SUPERSEDED` by the ADR
(an `EventSpec` is hypothesis-bound, not a server-known definition) and leaves the inventory (124 -> 123 atoms). Implementation
stays 99 `COMPLETE`, so it reads 99 / 123 = 80.5%; nothing is implemented and the server still passes no feature identity.

Earlier, DG-M registration (issue #453): five candidate atoms `M01`-`M05` are added as `OPEN_BLOCKING`/`MISSING` under the DG-M design
gate (#454, ADR-0073). No existing atom changes decision or implementation state; the percentages fall because the
inventory grows from 119 to 124 atoms (semantic 89.9% -> 86.3%; implementation 83.2% -> 79.8%), not because anything
regressed.

DG-C / A14 v1 closeout (#442): six rows (`A14`, `A14a`-`A14e`) move to
`COMPLETE` on `implement/l2` (78 -> 84); no new atom or decision-state change.
The golden (#413/#430) and disposal tool (#425/#426) add no atom.
See [SCOPE.md](../../SCOPE.md) for the evidence and executable inventory check,
and [DG-C triggers](../architecture/OPEN_DECISIONS.md#l2-v1-reopening-triggers-442)
for the eight conditions, none authorized.

Earlier, DG-C / A14 design-gate reconciliation (#371): ADR-0070 is accepted (#367), so `A14` and `A14a`-`A14f`
move from `OPEN_BLOCKING` to `FROZEN` (92 -> 99 decided) and the follow-on atom `A14g` (`ob500`
projection, ADR-0070 §9) is registered `FROZEN`/`MISSING` (99 -> 100 decided; 110 -> 111 atoms).
At that design-gate reconciliation implementation stayed `MISSING`; `IMPLEMENTATION_COMPLETE` was unchanged at 78.

Earlier, DG-C / A14 registration (issue #364): six candidate atoms `A14a`, `A14b`, `A14c`, `A14d`, `A14e` and
`A14f` are added as `OPEN_BLOCKING`/`MISSING` under the L2 design gate (`ADR-0070`, not yet written).
No existing atom changes decision or implementation state; the dependency edge `A13` -> `A14` is
removed by the owner decision of 2026-10-14. The percentages fall because the inventory grows, not
because anything regressed (104 -> 110 atoms; semantic 88.5% -> 83.6%; implementation 75.0% -> 70.9%).

Earlier, DG-K2 closeout (issue #409): the seven atoms `S01`, `S02`, `S03`, `S04`, `S05`, `S06` and `S07` move from `MISSING` to
`COMPLETE` (86 -> 93), each within the bound recorded in its row. No decision state changes, so the semantic score stays at
88.4%. The credit is for the composition, the credential and trust operations, the deck workload module, the units and the
runbook, and the two-part proof as it ran; it is not a claim that the topology is deployed or that the supervised proof
covered more than one happy path (see the row bounds and `SCOPE.md`).

Earlier, DG-K2 reconciliation (issue #389): ADR-0072 is `ACCEPTED`; `S01`-`S07` move from `OPEN_BLOCKING` to `FROZEN` (92 -> 99
semantic, 82.1% -> 88.4%; `OPEN_BLOCKING` 11 -> 4). Their implementation state stays `MISSING` (86 / 112 = 76.8%
unchanged); `S08` stays `OPEN_DEFERABLE`.

Earlier, DG-K2 registration (issue #382): eight candidate atoms `S01`-`S07` (`OPEN_BLOCKING`) and `S08`
(`OPEN_DEFERABLE`) are added, all `MISSING`, under the DG-K2 design gate (#383). No existing
atom changes state; the lower percentages reflect a larger inventory, not lost progress
(104 -> 112 atoms; semantic 88.5% -> 82.1%; implementation 82.7% -> 76.8%).

Earlier, DG-K closeout (#381): the eight `P` atoms `P01`, `P02`, `P03`, `P04a`, `P04b`, `P05a`,
`P05b` and `P06` move from `MISSING` to `COMPLETE` (78 -> 86), each within the
bound recorded in its row. No decision state changes, so the semantic score stays
at 88.5%. The credit is for libraries, wire families, the worker host and the deck
protocol client; it is not a claim that the topology is deployed (see the row
bounds and `SCOPE.md`).

Earlier, DG-K design-gate reconciliation (#319): ADR-0068 and ADR-0069 are accepted, so
the eight `P` atoms move from `OPEN_BLOCKING` to `FROZEN` (84 -> 92 decided).
Implementation stayed `MISSING` until the closeout above.

Earlier, DG-K registration (issue #318): eight candidate atoms `P01`, `P02`, `P03`,
`P04a`, `P04b`, `P05a`, `P05b` and `P06` are added as `OPEN_BLOCKING`/`MISSING`
under the DG-K design gate (#319). No existing atom changes state; the lower
percentages reflect a larger inventory, not lost progress (96 -> 104 atoms;
semantic 87.5% -> 80.8%; implementation 81.3% -> 75.0%).

Earlier, Wave 8 closeout (issue #327): the nine Wave 8 atoms `J03`, `J09`, `J10`, `J12`,
`J13`, `J14`, `J15`, `K12` and `K13` moved from `MISSING` to `COMPLETE`
(69 -> 78). No decision state changed, so the semantic score stays at 87.5%.
Each row states the bound of its credit; in particular `J10`/`J12`/`J13` are
in-process seams not reachable over J02, and `J03` has no dispatcher or worker.

Historical note from the `DG-J` reconciliation (issue #288): all six `DG-J` gates reached a disposition on 2026-10-04: `J03`, `J09`, `J10`,
`J12`, `J13`, `J14`, `J15`, `K12` and `K13` moved from `OPEN_BLOCKING` to
`FROZEN` under ADR-0062 through ADR-0067 respectively (ADR-0065 covers `J10`,
`J12` and `J13`). `J11` (the Replay Consumer-API seam) moved from
`OPEN_BLOCKING` to `OPEN_DEFERABLE`: ADR-0065 §5 explicitly defers it pending a
future ADR that defines a semantic, immutable feature/provider reference —
there is no accepted contract to freeze yet, so it is not counted as resolved.
No atom above moved to `COMPLETE`; implementation remains `MISSING` for all
nine newly frozen atoms. The jump from 78.1% to 87.5% is nine real decisions
closing, not implementation progress — `IMPLEMENTATION_COMPLETE` is unchanged
at 71.9%. This score must not be increased further by prematurely deciding
second-provider, L1/L2/L3, RL, or unrelated operational semantics, and `J11`
must not be frozen without its own accepted ADR.

The selected Live Ingest scope also carried one explicit **scope-level DG-B proposition** that is not a new atom and therefore is not included in the 87-atom counts: remediation of live gaps that exceed the bounded provider reconciliation window. It is now disposed (`NO_AUTHORITATIVE_REPAIR_PATH_PROVEN`, #110) -- see DG-B below -- and remains `OPEN_BLOCKING` as a standing rule for claiming any such gap filled/lossless, while A11/K10 are allowed to record the gap explicitly and continue with a new governed segment.

Decision states:

- `FROZEN` — contract/semantic authority is accepted and versioned.
- `RESOLVED` — planning/ownership/architecture proposition is decided; implementation may still be missing.
- `OPEN_BLOCKING` — must be resolved before the dependent atom on its path is authorized.
- `OPEN_DEFERABLE` — deliberately postponed until its stated evidence trigger exists.

Implementation states are separate: `COMPLETE`, `PARTIAL`, `MISSING`.

## Architecture spines

```text
A  Data & Acquisition
B  Data Access
C  Application
D  Representation
E  Feature
F  Research & Evaluation
G  Strategy & Decision
H  Execution & Portfolio
I  Experiment / ML / RL
J  Runtime & Interface
K  Operations
P  Process Topology (DG-K; ADR-0068/ADR-0069)
S  Server/deck composition (DG-K2)
M  Feature identity (DG-M)
T  Multi-instrument trades (DG-N)
O  Live publication (DG-O)
```

The implemented ownership direction remains governed by ADR-0024 and architecture tests. `application` composes existing capabilities and owns no quantitative meaning.

## Atomic capability inventory

### A — Data & Acquisition

| ID | Capability | Owner | Requires | Unlocks | Decision | Impl | Acceptance / authority |
|---|---|---|---|---|---|---|---|
| A01 | Canonical trade record | Data Plane | — | A02,A06,A07 | FROZEN | COMPLETE | Deterministic `trade-v1`; ADR-0018/Core |
| A02 | Dataset/partition identity | Data Plane | A01 | A03,A04,A05 | FROZEN | COMPLETE | Stable manifests; ADR-0018/25 |
| A03 | Source-acquired lineage | Data Plane | A02 | A08,A09 | FROZEN | COMPLETE | No synthetic parent; ADR-0025 |
| A04 | Declared coverage | Data Plane | A02 | A08,B02,B04 | FROZEN | COMPLETE | Explicit half-open coverage; ADR-0022 |
| A05 | Catalog/schema bootstrap | Data Plane | A01,A02 | A09,B02 | RESOLVED | COMPLETE | Conflict-refusing registration; Core/CM |
| A06 | Canonical Parquet materialization | Producer | A01,A02 | A08 | FROZEN | COMPLETE | Deterministic physical artifact; ADR-0023 |
| A07 | Bybit historical acquisition | Source/Producer | A01 | A08,K06 | FROZEN | PARTIAL | One-UTC-day BTCUSDT-linear v1 profile; multi-day consumers loop day-by-day; Ingest/ADR-0023/ADR-0054 |
| A08 | S13 certification | Producer | A03,A04,A06,A07 | A09,A16 | FROZEN | COMPLETE | Durable fail-closed evidence; ADR-0023 |
| A09 | S14 publication | Producer | A05,A08 | B02,A10 | FROZEN | COMPLETE | Eligible partition becomes readable; ADR-0023 |
| A10 | Backfill and repair | Data Plane | A09,A16 | A11 | FROZEN | COMPLETE | Deterministic repair-intent/candidate/cutover; ADR-0033 |
| A11 | Live trades acquisition | Data Plane | A10,K05,K06,K08 | B06,K10,J07 | FROZEN | COMPLETE | ADR-0040; PR #112 implements Bybit BTCUSDT mapping/session/dedup/cutover/reconnect and canonical publication seam; PR #113 proves the real-provider/server publication path |
| A12 | Second-venue trades | Data Plane | A11,real provider evidence | C06 | OPEN_DEFERABLE | MISSING | Venue ordering/eligibility profile; evidence-triggered |
| A13 | L1 contract/acquisition | Data Plane | A01,A04,real feed | L1 consumers | OPEN_BLOCKING | MISSING | Versioned schema/ordering/provenance; DG-C L1 branch. Not a prerequisite of `A14` (owner decision 2026-10-14, #364) |
| A14 | L2 contract/acquisition | Data Plane | A01,A04,real feed | A14a-A14g,A15 | FROZEN | COMPLETE | ADR-0070 (#367), Amendments 1 (#402 / PR #403) and 2 (#416 / PR #417). Batch v1 CLOSED on `implement/l2` by governance #442: A14a-A14e COMPLETE (PR #375, #379, #401, #411 + #417, #408) and golden accepted (PR #413 + #430, `1cdd78f`, 11/11 claims). Historical Bybit BTCUSDT `ob200` ingest through consumer read, with explicit gaps; no live L2 or `ob500`. Independent of `A13`; `A14f` and `A14g` remain held outside this closure. Promotion to `main` is a separate wave closeout PR |
| A14a | L2 ordering provider and certification profile v1 | Data Plane | A14 | A14b,A14c | FROZEN | COMPLETE | ADR-0070 §1, §2, §5, §7; #374 / PR #375. Numeric `seq` ordering, `exchange_ts = cts`, `provider_ts = ts`, `u + 1` continuity and fail-closed certification profile. E2-E4 measured on one full `ob200` day (#430): no restart, mid-day re-snapshot, absent-level delete or crossed state; restart survival and population rates remain unproven; thresholds unchanged |
| A14b | Canonical L2 Parquet materializer v1 | Data Plane | A14a | A14c,A14d | FROZEN | COMPLETE | ADR-0070 §3, §4 and Amendment 1; #376 / PR #379. Source-event Parquet materializer/scanner; `uint64` order columns round-trip exactly to the string-typed schema; `dt=YYYY-MM-DD/seg=NNN`, byte-identical reproduction. No reconstructed book or trade-path change; `raw` support remains an A14g requirement |
| A14c | L2 publication and catalog bridge v1 | Producer | A14a,A14b,A08,A09 | A14d,A14e | FROZEN | COMPLETE | ADR-0070 §4, §5 and Amendment 1; #399 / PR #401. L2 certification, publication and catalog bridge, `kind="l2"`; one clean anchored run per eligible partition, published `valid`, continuity evidence bound to bytes; PostgreSQL proof requires no catalog DDL change |
| A14d | Bybit historical L2 ingest v1 | Source/Producer | A14b,A14c,A07 | A14f,A14g | FROZEN | COMPLETE | ADR-0070 §2, §4, §7 and Amendments 1-2; #410 / PR #411, conformed by #416 / PR #417. Historical `ob200` ingest cuts discontinuities into `dt=.../seg=NNN` runs. UTC day owns events by `exchange_ts`, strictly congruent with `trade-v1`; contiguous source-head/tail blocks are retained and evidenced, never published into the adjacent day automatically. Explicit head/tail `known_gap`; no blanket refusal for a valid post-midnight tail, but interior out-of-day placement/regression still refuses. Byte-identical re-acquisition; no `ob500` |
| A14e | L2 consumer read v1 | Data Access | A14c,B02 | A14 closure | FROZEN | COMPLETE | ADR-0070 §6; #407 / PR #408. `l2_book_events@1` under `j02.market_data.read`; numeric sequence order, coverage/gaps/anchored flag, per-partition source-acquired/projected origin; gaps never filled, no projected-row `u + 1` assumption, byte-based `result_too_large` bound. Frozen J02 v1 and trade path unchanged |
| A14f | Bybit live L2 ingest v1 | Data Plane | A14d,A11 | — | FROZEN | MISSING | ADR-0070 §8 and Amendment 1 item 9. Websocket `orderbook.200` ingest on the live-ingest host; a reconnect or re-snapshot opens a new run; no repair path (ADR-0044); the day roll writes no synthetic snapshot and the continuation head is anchored by predecessor-linked evidence whose mechanics are fixed in its own issue; held behind the `SCOPE.md` Part B start gate (batch v2; outside `A14` closure) |
| A14g | Bybit `ob500` raw ingest and top-200 projection v1 | Source/Producer | A14b,A14c,A14d | — | FROZEN | MISSING | ADR-0070 §9. Registered by #371. Raw `ob500` is kept lossless; a deterministic top-200 event projection with per-partition lineage (`derived_from`, `transform`) is certified by recomputation (check 9); outside the `A14` closure; held behind the `SCOPE.md` Part B start gate |
| A15 | L3/MBO contract | Data Plane | real L3 feed | advanced research | OPEN_DEFERABLE | MISSING | Map real feed without semantic invention |
| A16 | General quality lifecycle | Data Plane | A04,A08 | A10,A11 | RESOLVED | COMPLETE | Deterministic lifecycle for first vertical + A10 reuse; lifecycle beyond first vertical remains open |

### B — Data Access

| ID | Capability | Owner | Requires | Unlocks | Decision | Impl | Acceptance / authority |
|---|---|---|---|---|---|---|---|
| B01 | Data Access boundary | Data Access | A05 | B02 | RESOLVED | COMPLETE | Canonical `quant_platform.access`; ADR-0019/24 |
| B02 | Bounded historical scan | Data Access | A09,B01 | C02,D03 | FROZEN | COMPLETE | Ordered memory-bounded scan; DATA_GATEWAY/ADR-0023 |
| B03 | Result identity/provenance | Data Access | B02 | B05,F04,B06 | FROZEN | COMPLETE | Semantic/source change changes identity; DATA_GATEWAY |
| B04 | Non-contiguous coverage read | Data Access | A04,A09 | A10,A11 | FROZEN | COMPLETE | Explicit `ALLOW_PARTIAL` coverage reads; ADR-0029 |
| B05 | Durable DatasetSnapshot | Data Access | B03 | experiment replay | OPEN_DEFERABLE | MISSING | Immutable reproducible reference; trigger on durable replay need |
| B06 | Live access/cursor | Data Access | A11,B03 | D04,J07 | FROZEN | COMPLETE | ADR-0047; deterministic consumer resume/replay, disconnect and explicit gap notification; PR #204 implementation; PR #212 Golden proof |
| B07 | In-process batch surface | Data Access | B02 | C02,D03 | RESOLVED | COMPLETE | Existing bounded `DataScan`; concrete naming local |
| B08 | Schema evolution policy | Data Access | new schema evidence | A13-A15 | OPEN_DEFERABLE | MISSING | Resolve when a new real schema/version appears |

### C — Application

| ID | Capability | Owner | Requires | Unlocks | Decision | Impl | Acceptance / authority |
|---|---|---|---|---|---|---|---|
| C01 | Application ownership | Application | B01 | C02 | RESOLVED | COMPLETE | `quant_platform.application`; ASS-01/ADR-0024 |
| C02 | ASS-02 semantic selector resolution | Application | C01,B02,D01,J01 | C03 | FROZEN | COMPLETE | Semantic request resolves without storage identity; Consumer API |
| C03 | ASS-02 result/error translation | Application | C02 | C04,J02,J04-J06 | FROZEN | COMPLETE | PR #41; stable envelope/error semantics; exact-head integrity #91 PASS |
| C04 | Tool orchestration convergence | Application | C02,C03,C05 | governed entry points | RESOLVED | COMPLETE | Exact ASS-03 debt reaches zero; issue #40/PR #44, closed 2026-09-14 |
| C05 | Configuration convergence | Engineering/Application | C01 | C04,K02 | RESOLVED | COMPLETE | CLI > env > declared default > fail; typed immutable capability-specific config; Application owns composition |
| C06 | Multi-capability resolution | Application | second venue/representation evidence | general application service | OPEN_DEFERABLE | MISSING | Unsupported combinations explicit; no speculative registry |

### D — Representation

| ID | Capability | Owner | Requires | Unlocks | Decision | Impl | Acceptance / authority |
|---|---|---|---|---|---|---|---|
| D01 | Representation identity | Representation | — | C02,D02,E02 | RESOLVED | PARTIAL | Separate from DatasetIdentity; Consumer/Core |
| D02 | CandleDefinition v1 | Representation | A01,D01 | D03,D04 | FROZEN | COMPLETE | Accepted definition/hash/schema semantics; ADR-0021 |
| D03 | Historical candle computation | Representation | B02,D02 | D05,E03 | FROZEN | COMPLETE | Reproducible CLOSED candles on demand |
| D04 | Incremental/live candles | Representation | B06,D02 | J07 | FROZEN | COMPLETE | PR #205 implements PARTIAL/CLOSED live candle computation; PR #208 composes B06 into D04; PR #212 Golden proof |
| D05 | Candle materialization identity | Representation | D02,D03,A02,A03 | research reuse | FROZEN | MISSING | ADR-0059 freezes representation-artifact identity and replay-input-profile shape; runtime/materialization/replay access not implemented |
| D06 | Footprint representation | Representation | A01,D01 | E06 | FROZEN | COMPLETE | FootprintDefinition v1 exact duration/tick grid/sparse levels/finality; ADR-0027 |

### E — Feature

| ID | Capability | Owner | Requires | Unlocks | Decision | Impl | Acceptance / authority |
|---|---|---|---|---|---|---|---|
| E01 | Definition/set/artifact separation | Feature Engine | D01 | E02-E04 | FROZEN | COMPLETE | Distinct lifecycle identities; ADR-0016 |
| E02 | FeatureDefinition v1 | Feature Engine | E01,D01 | E03,E04,E06 | FROZEN | COMPLETE | Versioned semantics + availability + identity; ADR-0026 |
| E03 | FeatureSet catalog/provider | Feature Engine | E01,E02 | E04 | RESOLVED | PARTIAL | Ordered provider resolution; ADR-0016/CM |
| E04 | FeatureArtifact/materialization | Feature Engine | E02,E03,A02 | F01,I04,E06 | FROZEN | COMPLETE | Deterministic identity, SupportShape, FINAL-only sealing; ADR-0034 |
| E05 | H01 pure imbalance kernel | Feature Engine | Legacy Harvest audit | E06 | RESOLVED | COMPLETE | Narrow ADOPT numeric/edge semantics |
| E06 | H01 canonical integration | Feature Engine | D06,E02,E04,E05 | H01-backed research | FROZEN | COMPLETE | ADR-0035; implemented, issue #83/PR #88 |
| E07 | Custom/provider extension | Feature Engine | E02,real second-provider need | broader library | OPEN_DEFERABLE | MISSING | Let second provider drive minimum extension seam |

### F — Research & Evaluation

| ID | Capability | Owner | Requires | Unlocks | Decision | Impl | Acceptance / authority |
|---|---|---|---|---|---|---|---|
| F01 | HypothesisSpec | Research | E02 | F02,F03 | RESOLVED | COMPLETE | Reproducible hypothesis references canonical observables |
| F02 | EventSpec/detection | Research | F01,E04 | F04 | RESOLVED | COMPLETE | Traceable event rule + availability evidence; implemented, issue #85/PR #89 |
| F03 | OutcomeSpec/Outcome | Research | F01 | F04,F07 | FROZEN | COMPLETE | ADR-0038 canonical Outcome v1 authority; issue #86/PR #90 + issue #102/PR #103 closeout |
| F04 | Event study/sweeps | Research | F02,F03,B03 | I01,F08 | RESOLVED | COMPLETE | Re-run preserves population and aggregates; issue #92/PR #94 |
| F05 | Walk-forward schedule | Validation | shared time primitives | F06,F08 | RESOLVED | COMPLETE | Deterministic temporal fold boundaries |
| F06 | Availability/purge/embargo | Validation | F05,E02 | F07,I04 | FROZEN | COMPLETE | Adversarial leakage rejected; ADR-0031 |
| F07 | Labels/censoring/lockbox | Validation | F03,F06 | G01,I04 | FROZEN | COMPLETE | ADR-0036; issue #96/PR #99 |
| F08 | DSR/PBO | Research/Validation | F04,F05 | robust comparison | FROZEN | COMPLETE | ADR-0037; issue #98/PR #104 |

### G — Strategy & Decision

| ID | Capability | Owner | Requires | Unlocks | Decision | Impl | Acceptance / authority |
|---|---|---|---|---|---|---|---|
| G01 | StrategySpec/DecisionIntent | Strategy | F07 | G02,H01 | RESOLVED | COMPLETE | Same inputs/config => same intent/provenance; integrated in Wave 4 |
| G02 | Policy composition | Strategy | G01 | G03,G04 | RESOLVED | COMPLETE | Deterministic policy composition integrated in Wave 4 |
| G03 | Risk/sizing | Strategy | G01,G02 | H01 | RESOLVED | COMPLETE | Reproducible limits/sizing evidence integrated in Wave 4 |
| G04 | Session/cooldown semantics | Strategy | G02,F06 | H01,H03 | FROZEN | COMPLETE | ADR-0045; DST/session/cooldown vectors |

### H — Execution & Portfolio

| ID | Capability | Owner | Requires | Unlocks | Decision | Impl | Acceptance / authority |
|---|---|---|---|---|---|---|---|
| H01 | Order/Fill lifecycle | Execution | G03,G04 | H02,H04 | RESOLVED | COMPLETE | Illegal transitions rejected; integrated in Wave 4 |
| H02 | Cost/synthetic-fill model | Execution | H01 | H04,H05 | RESOLVED | COMPLETE | Pinned fee/slippage/fill scenarios integrated in Wave 4 |
| H03 | Execution conflict model | Execution | H01,G04 | H04,H05 | FROZEN | COMPLETE | ADR-0046; same-bar/OCO/partial-fill single result |
| H04 | Portfolio/ledger | Portfolio | H01,H02,H03 | H05,I03 | RESOLVED | COMPLETE | Accounting identities hold across replay; integrated in Wave 4 |
| H05 | Deterministic replay | Execution | H02,H04 | J07,I03 | RESOLVED | COMPLETE | PR #160; replay identity binds data/strategy/execution model |
| H06 | Multi-asset execution | Portfolio | H04,real product need | advanced strategy/RL | OPEN_DEFERABLE | MISSING | Resolve on concrete multi-asset scope |

### I — Experiment / ML / RL

| ID | Capability | Owner | Requires | Unlocks | Decision | Impl | Acceptance / authority |
|---|---|---|---|---|---|---|---|
| I01 | Study/Trial/Run/Artifact semantic model | Experiment System | Core concepts | I02,I03 | RESOLVED | COMPLETE | One canonical experiment identity family |
| I02 | Experiment persistence | Experiment System | I01,A05 | I03,J03 | RESOLVED | COMPLETE | One restart-safe canonical persistence model |
| I03 | Trial accounting/comparison | Experiment System | I01,I02,H05 | model selection | RESOLVED | COMPLETE | PR #178 / issue #171; resume idempotent, comparable population identity and metric comparison |
| I04 | Supervised input/selection | Learning | E04,F06,F07 | I05 | RESOLVED | COMPLETE | PR #177 / issue #172; durable split/provenance/anti-leakage evidence |
| I05 | Supervised training/evaluation | Learning | I03,I04 | J07 | RESOLVED | COMPLETE | PR #179 / issue #173; run binds code/data/splits/model/metrics and emits model/prediction/metric artifacts |
| I06 | Strategic RL contract | Strategic RL | G01,F07,I01 | RL runtime | OPEN_BLOCKING | MISSING | State/action/reward excludes execution-control task; DG-G strategic-RL branch |
| I07 | Execution RL contract | Execution RL | H01-H05,I01 | RL runtime | OPEN_BLOCKING | MISSING | Structurally separate execution task; DG-G execution-RL branch |

### J — Runtime & Interface

| ID | Capability | Owner | Requires | Unlocks | Decision | Impl | Acceptance / authority |
|---|---|---|---|---|---|---|---|
| J01 | Consumer API semantics | Application/API | D01 | C02,J02 | FROZEN | COMPLETE | Stable semantic selector/result/error boundary; ADR-0020 |
| J02 | Canonical API transport | API Runtime | C03,real client need | J04-J06 | RESOLVED | COMPLETE | ADR-0050; WebSocket transport preserves Consumer API semantics without domain/storage logic. Amendment 1 froze a 50,000-row/16 MiB bound; auth/TLS for non-loopback remains open (see J09/DG-J G1) |
| J03 | Job runtime | Runtime | C03,I02 | long operations | FROZEN | COMPLETE | ADR-0062 durable admission record, deterministic `job_id`, §3 lifecycle, attempt evidence, no automatic retry and restart -> `RECOVERY_REQUIRED` implemented by PR #307 / issue #296 (`application/durable_jobs.py`, SQLite store). Handler dispatch, worker process and PostgreSQL store are not claimed (DG-K P02/P04) Since DG-K (#381): PostgreSQL store (P02), dispatch registry (P04a, empty) and single worker (P04b) exist; no data-local handler ships. |
| J04 | CLI client | Client Layer | J02 or explicitly bounded in-process C03 | operator workflow | RESOLVED | COMPLETE | Thin client; no quantitative/storage logic |
| J05 | TUI client | Client Layer | J02 | interactive workflow | RESOLVED | COMPLETE | Same canonical semantics |
| J06 | App UI | Client Layer | J02 | product workflow | RESOLVED | COMPLETE | Business logic remains behind service boundary |
| J07 | Paper/shadow mode | Runtime | A11,H05,I05,J02,K03 | J08 | RESOLVED | MISSING | Same decisions, simulated routing, explicit evidence |
| J08 | Live product mode | Runtime/Operations | J07,K02,K03,K05,K06,K08,K09,K10 | production | RESOLVED | MISSING | Explicit authorization/audit/recovery gates |
| J09 | Authenticated/TLS transport v1 | API Runtime | J02 | K12,K13,J10-J13,J15 | FROZEN | COMPLETE | ADR-0063 WSS+TLS 1.3+mTLS non-loopback J02, exact-fingerprint principal mapping, `j02.market_data.read` scope and security evidence implemented by PR #310 / issue #297. Only `j02.market_data.read` exists Since DG-K (#381): the per-family scopes `j02.strategy.compose`, `j02.validation.evaluate`, `j02.training.evaluate`/`.register`, `j02.jobs.submit`/`.read`, `j02.admitted_input.read` and `j02.result.submit` are enforced before decode (P05a/P05b/P06); the server executable does not yet configure TLS/mTLS. |
| J10 | Strategy Consumer-API seam v1 | Application/API | C03,G01-G04,J02 | J04-J06 extension | FROZEN | COMPLETE | ADR-0065 §2 `strategy-compose-v1` implemented as an in-process application seam by PR #308 / issue #300. Not reachable over J02 (no wire message or remote scope; owner decision 2026-10-08 assigns that to DG-K P05) Since DG-K (#381): carried over J02 by `j02-strategy-v1` (P05a), opt-in handler; no host composes it. |
| J11 | Replay Consumer-API seam v1 | Application/API | C03,H05,J02 | J04-J06 extension | OPEN_DEFERABLE | MISSING | ADR-0065 §5 explicitly defers `DG-J` G4's Replay seam: `HistoricalReplayRuntime.run()`'s injected `feature_provider` callable has no accepted identity-bearing, serializable, server-resolved definition yet. Owner decision 2026-10-10 (DG-K, #318): not planned; replay runs only on the deck over K12-admitted inputs, results return through K13 (P06). Trigger: an explicit maintainer request for server-side or remote-client replay, then a future ADR defining that feature/provider reference and whether the operation is synchronous or J03-admitted |
| J12 | Validation Consumer-API seam v1 | Application/API | C03,F06-F08,J02 | J04-J06 extension | FROZEN | COMPLETE | ADR-0065 §3 and Amendment 1 finite Validation seam (folds, classification, DSR, bounded synchronous PBO) implemented in-process by PR #312 / issue #301. Not reachable over J02 (DG-K P05) Since DG-K (#381): carried over J02 by `j02-validation-v1` (P05a), opt-in handler; no host composes it. |
| J13 | Training Consumer-API seam v1 | Application/API | C03,I03-I05,J02 | J04-J06 extension | FROZEN | COMPLETE | ADR-0065 §4 and Amendment 2 bounded `supervised-train-evaluate-v1` seam implemented in-process by PR #315 / issue #302, including RunIdentity/projection provenance refusal. Not reachable over J02 (DG-K P05) Since DG-K (#381): carried over J02 by `j02-training-v1` (P05a, ADR-0069 Amendment 2), opt-in handler; no host composes it. |
| J14 | Streaming/live transport extension v1 | API Runtime | J02 | future live consumer seams | FROZEN | COMPLETE | ADR-0066 `j14-framed-result-v1` framing (RFC 8785, per-chunk/transfer SHA-256, transfer-local resume, bounded retention) implemented by PR #320 / issue #303 (`application/framed_result_transport.py`). Not yet dispatched by the J02 server; no live family Since DG-K (#381): dispatched for admitted-input member transfers and job results; the transfer store is in-memory (ADR-0064 Amendment 1 point 7). |
| J15 | Omega API client adapter v1 | Client Layer | J02,J09,PUBLIC_PYTHON_API.md | first real remote-API client | FROZEN | COMPLETE | ADR-0067 Omega remote client implemented outside `src/quant_platform` (`clients/omega`) by PR #321 / issue #304: J02 only, `j02.market_data.read`, `wire_incompatible`, `remote_security_failure`. No capability beyond market data |

### K — Operations

| ID | Capability | Owner | Requires | Unlocks | Decision | Impl | Acceptance / authority |
|---|---|---|---|---|---|---|---|
| K01 | Provisioning/fixtures/CI | Engineering | — | all spines | RESOLVED | COMPLETE | Reproducible validation environment |
| K02 | Server access/runtime identity | Operations | C05,K01 | J08 | FROZEN | COMPLETE | ADR-0041; PR #113 proves non-root least privilege, minimum catalog rights, physical storage topology and backup-authority separation on the target server |
| K03 | Observability | Operations | K01 | J07,J08,K10 | RESOLVED | COMPLETE | Minimum externally observable health/provenance/failure transitions |
| K04 | Capacity observation | Operations | A05,K01 | K05 | RESOLVED | COMPLETE | Report exact usage/free space without control action |
| K05 | Health/pressure policy | Operations | K04 | A11,K06,K07 | FROZEN | COMPLETE | Explicit threshold/time-to-full action; ADR-0028 |
| K06 | RAW/source protection | Operations/Data Plane | A07,K05 | A11,K07,K08 | FROZEN | COMPLETE | Protection-identity/assessment seam, attributable-evidence; ADR-0032 |
| K07 | Tier relocation | Operations/Data Plane | K05,K06 | storage lifecycle | FROZEN | COMPLETE | ADR-0048; crash yields old or new valid placement; PR #209 implementation; PR #212 Golden proof |
| K08 | Backup/restore proof | Operations | K06 | K09,J08,K10,A11 | FROZEN | COMPLETE | ADR-0039; PR #111 implements identity-bound recovery/isolated restore and PR #113 closes the real deployment-independence evidence handoff |
| K09 | Retention/deletion authority | Operations | K08 | sustainable live | FROZEN | COMPLETE | ADR-0049; never delete protected/sole recoverable evidence; PR #211 implementation; PR #212 Golden proof |
| K10 | Checkpoint/recovery | Operations/Data Plane | A11,K03,K08 | J08 | FROZEN | COMPLETE | ADR-0042; PR #114 implements persisted checkpoint/recovery + full hermetic proof matrix; PR #122 closes the bounded real-server restart/deployed checkpoint proof (issues #109, #121) |
| K11 | Governance-state consistency | Governance | K01 | reliable planning | RESOLVED | COMPLETE | Canonical docs represent accepted state without ambiguity; Wave 4 closeout reconciled by governance issue #147 |
| K12 | Server-owned admitted-input export/reference v1 | Operations | K02,B01-B04 | deck/consumer-machine compute | FROZEN | COMPLETE | ADR-0064 §2 sealed `AdmittedInputManifestV1`, `admission_id` and `ADMITTED`..`DELIVERED` state machine implemented by PR #309 / issue #298 (`application/admitted_input.py`, SQLite store). Physical byte transfer to the deck and a deck-side reader are not claimed Since DG-K (#381): PostgreSQL store with delivery-attempt identity and byte delivery over `j02-admitted-input-v2` (P06); the production authority seams (`resolve`/`members`/`read_member`) are not implemented. |
| K13 | Governed experiment-result import/registration v1 | Operations | K12,I01,I02 | attributable deck results | FROZEN | COMPLETE | ADR-0064 §3 full-bundle refusal, idempotent re-submission and distinct identities implemented by PR #311 / issue #299 (`application/governed_result_import.py`). Experiment write (PostgreSQL) and evidence record (SQLite) are not one transaction; DG-K P02 must establish it Since DG-K (#381): registration is one PostgreSQL transaction (P02) and submission is carried by `j02-result-import-v1` (P06); the service is not composed in a host and no output-contract registry exists. |

### P — Process Topology (DG-K; ADR-0068, ADR-0069)

Registered by governance issue #318 and frozen by the design gate #319
(ADR-0068 process topology v1, ADR-0069 J02 carriage of consumer seams and
deck handoff v1), which kept the registered names. `SCOPE.md` Part B
authorized them; all are implemented and credited by the closeout #381, each within the bound its row records.

| ID | Capability | Owner | Requires | Unlocks | Decision | Impl | Acceptance / authority |
|---|---|---|---|---|---|---|---|
| P01 | Canonical identity serializer v1 | Engineering/Application | — | P04a, identities computed in several processes | FROZEN | COMPLETE | ADR-0068 §5. One module with named, versioned byte profiles; every existing identity site declares its profile; golden-hash parity on every existing identity vector (including #313's replay vectors); no identity changes. RFC 8785 is one profile, for J14 and new contracts only Credit (#381): PR #344 (issue #330), `quant_platform.canonical`; byte parity on the pinned identity vectors and the two-`PYTHONHASHSEED` cross-process proof are in `tests/test_canonical_v1.py`. |
| P02 | Runtime store on PostgreSQL v1 | Operations/Application | J03,K12,K13 | P04a,P06 | FROZEN | COMPLETE | ADR-0068 §4. `runtime` schema and DB role for the J03/K12/K13 stores (today SQLite); establishes atomic K13 registration with the Experiment write (today PostgreSQL then SQLite, `application/governed_result_import.py:268-288`) Credit (#381): PR #349 (issue #331: `runtime` schema, unit-scoped roles, J03/K12/K13 stores on PostgreSQL), PR #350 (issue #332: atomic K13 registration), PR #355 (issue #354: a concurrent identical resubmission is refused). Bound: the confirmation that no production SQLite runtime state exists is operational evidence, not code, and is recorded by the maintainer (#381). |
| P03 | Executable host boundaries v1 | Engineering | — | P04b | FROZEN | COMPLETE | ADR-0068 §7. Hosts stay in `tools/` (owner decision; ADR-0043 unchanged); per-host import rules in `tests/test_package_boundaries_v1.py`; `clients/*` stay outside `tools/` and `src/quant_platform` Credit (#381): PR #345 (issue #333), per-host import rules in `tests/test_package_boundaries_v1.py`; no host moved. |
| P04a | J03 handler registry and dispatch v1 | Application | P01,P02 | P04b | FROZEN | COMPLETE | ADR-0068 §6. Closed, declared, data-local handler set inside `quant_platform.application` (owner decision: no replay/sweep/training handlers on the server); no generic executor (ADR-0062 §6) Credit (#381): PR #351 (issue #334), `application/job_dispatch.py`. Bound: the registry mechanism is complete; `DATA_LOCAL_JOB_REGISTRY` ships **empty**, so no data-local handler exists yet (each enters through its own implementation issue). |
| P04b | Single J03 worker process v1 | Runtime | P03,P04a | P05b | FROZEN | COMPLETE | ADR-0068 §6, §8. Exactly one worker process over the PostgreSQL-backed queue; no broker; restart recovery stays correct with one worker; evidence trigger recorded for more than one (ADR-0062 §3/§4) Credit (#381): PR #352 (issue #335), `tools/job_worker.py` with the PostgreSQL advisory lock and the process-lifetime lock (ADR-0068 Amendment 1). Bound: with an empty handler registry it has nothing to run. |
| P05a | J02 carriage of synchronous J10/J12/J13 v1 | API Runtime | J02,J09 | J04-J06 extension | FROZEN | COMPLETE | ADR-0069 §1-§3. J02 wire messages and one remote scope per operation family (ADR-0063) without changing ADR-0065 semantics or J02 v1; J13 requests respect the 16 MiB bound (ADR-0050 §2) Credit (#381): PR #346 (issue #336, per-family dispatch and scope enforcement before decode), PR #347 (issue #337, `j02-strategy-v1`), PR #348 (issue #338, `j02-validation-v1`), PR #380 (issue #339, `j02-training-v1` with the J13 reconstruction codec of ADR-0069 Amendment 2 and the pre-decode `register` grant). Bound: application-owned codecs and opt-in family handlers proven over a real WSS/mTLS listener in tests; no executable composes them. |
| P05b | J02 submit/status/result for J03 data-local jobs v1 | API Runtime | P04b | remote data-local jobs | FROZEN | COMPLETE | ADR-0069 §4. Submit/status/result messages for data-local operations admitted through J03 Credit (#381): PR #353 (issue #340, `j02-job-v1`) and PR #359 (issue #358, multi-body results). Bound: opt-in handlers, not composed in a host, and the data-local registry is empty. |
| P06 | Deck handoff over J02 v1 | API Runtime/Operations | J09,J14,P02 | deck replay/training with governed results | FROZEN | COMPLETE | ADR-0069 §5-§8. K12 delivery of admitted input bytes (J14 framing for large payloads) and K13 result-bundle submission, one least-privilege scope each; deck-side client verifies delivered digests and submits the bundle; the K13 bundle carries the replay feature-provider code identity. ADR-0064 §4 defines no wire shape today Credit (#381): PR #356 (issue #341, K12 delivery, now `j02-admitted-input-v2`), PR #361 (issue #342, K13 import and feature-provider identity), PR #366 (issue #343, `clients/deck`), PR #373 (issue #357, delivery-attempt identity, admission-gated resume; ADR-0064 Amendment 1, ADR-0066 Amendment 1, ADR-0069 Amendment 1). Bound: protocol client and server-side transports with test authorities; no production K12 authority, no K13 composition in a host, no deck-side bundle producer, no deployment. |

### S — Server/deck deployment composition (DG-K2)

Registered by governance issue #382 after the DG-K closeout (#381); the design gate #383 produced ADR-0072, `ACCEPTED` on 2026-10-16 (#387), and governance issue #389 authorizes `S01`-`S07` for implementation on the integration branch `implement/dgk2`. `SCOPE.md` Part B records the start gates and the acceptance of each atom. No atom here is implemented; `S08` is not authorized.

| ID | Capability | Owner | Requires | Unlocks | Decision | Impl | Acceptance / authority |
|---|---|---|---|---|---|---|---|
| S01 | K12 server authority seams v1 | Application/Operations | P06 | S03,S07 | FROZEN | COMPLETE | ADR-0072 §1 (accepted; authorized by #389). `resolve`/`members`/`read_member` over the DataGateway/catalog for `j02-admitted-input-v2`; closed sets of accepted operation and profile identities; expiry policy values; byte and member bounds. Today only test authorities exist Credit (#409): PR #398 (issue #391), `application/admitted_input_authority.py`: `DatasetAdmittedInputAuthority` over the DataGateway, manifest built from the open scan metadata without reading a record, dataset selector only (`artifact_inputs_unavailable`). Bound: whole-file, in-memory delivery; the bounds were raised by ADR-0072 Amendment 1 (issue #431, PR #432) to 512 MiB per file, 1 GiB and 16 files per admission; `definition_identities` is empty (the server passes no feature identity). |
| S02 | K13 composition v1 | Application/Operations | P02,P06 | S03,S07 | FROZEN | COMPLETE | ADR-0072 §2 (accepted; authorized by #389). Closed output-role registry with `ResultOutputContractV1` (replay roles require `replay_feature_provider`), staging bounds, a `GovernedResultImportService` factory over the `runtime` role. Composed in no host today Credit (#409): PR #400 (issue #392), `application/result_import_composition.py`: the closed six-role registry, the staging bounds and the service factory over one autocommit connection. Bound: the 8 MiB output bound and the staging bounds were not measured on a real output. |
| S03 | API host composition v1 | API Runtime | S01,S02,P03,P05a,P05b | S06,S07 | FROZEN | COMPLETE | ADR-0072 §3 (accepted; authorized by #389). `tools/api_server.py` composes TLS/mTLS (ADR-0063), principals, all DG-K families and the J14 store, with fail-closed startup and per-host import rules. Today the host serves J02 v1 market data only Credit (#409): PR #405 (issue #395), `application/api_host_composition.py` and `tools/api_server.py`: opt-in families, TLS 1.3 mutual TLS, principals file, fail-closed startup, one shared J14 store; the default invocation is unchanged. Bound: `jobs` is composed without a result resolver (no handler exists). |
| S04 | Credential and trust operations v1 | Operations | — | S07 | FROZEN | COMPLETE | ADR-0072 §4 (accepted; authorized by #389). Issuance, rotation and revocation procedure and tooling under `infra/`, fingerprint-to-principal mapping and secret handling, within ADR-0063 §2: no credential service, IAM or bearer tokens Credit (#409): PR #414 (issue #393), `infra/pki/` and `docs/engineering/DECK_PKI_OPERATIONS.md`: offline CA, server and client certificates, fingerprints, restart-based rotation and revocation. Bound: no CRL or OCSP; the file-mode test is skipped where modes are not enforced and runs on Linux CI. |
| S05 | Deck workload library v1 | Client/Application | P06 | S07 | FROZEN | COMPLETE | ADR-0072 §5 (accepted; authorized by #389). Producer of `GovernedResultBundleV1` (deck code identity, feature-provider identity, input digests), submit-flow glue and commit pinning; `clients/deck/` keeps importing nothing from `quant_platform` Credit (#409): PR #418 (issue #394), `application/deck_result_bundle.py`: the producer of the K13 bundle from the verified admission, clean tree and server commit required. Bound: the provider identity is attribution evidence, not a server-defined feature definition; a bundle names one admission. |
| S06 | Deployment units and runbook v1 | Operations | S03,P04b | S07 | FROZEN | COMPLETE | ADR-0072 §6 (accepted; authorized by #389). systemd units and environment examples for the API and the worker, the ADR-0057 handoff runbook, the promotion procedure and the server/deck compatibility rule Credit (#409): PR #421 (issue #396), `infra/systemd/` units and environment examples, `docs/engineering/DECK_HANDOFF_RUNBOOK.md`, `docs/engineering/SERVER_DECK_COMPATIBILITY.md`. Bound: the import accepts a result against its own admission's commit, so an admission made before a promotion stays valid (an operator obligation, recorded); no memory limit on the API unit. |
| S07 | Real-topology proof v1 | Operations | S01,S02,S03,S04,S05,S06 | server/deck split | FROZEN | COMPLETE | ADR-0072 §7 (accepted; authorized by #389). PostgreSQL + API + worker + deck client over WSS/mTLS: admit, deliver, verify, acknowledge, run, upload, submit and register, plus the fault cases the gate defines. Defines "ready to split" Credit (#409): PR #427 (issue #397), part 1, the hermetic proof `PostgresSplitTopology` (14 tests, run by the CI job of the runtime stores), and PR #428, the record of part 2. Bound: part 2 is one happy path on the smallest real partition (3 122 bytes) executed by the maintainer and reported from the session log, on a pull-request commit; no fault was repeated there and the largest real day (252.8 MiB) was not delivered. The topology is deployable only within the bounds of ADR-0072 Amendment 1; see `docs/engineering/DGK2_SPLIT_TOPOLOGY_PROOF.md`. |
| S08 | First data-local J03 handlers v1 | Application | P04a,P04b | remote data-local jobs | OPEN_DEFERABLE | MISSING | Trigger: the maintainer names a first handler (feature/representation materialization, historical import or catalog maintenance). `DATA_LOCAL_JOB_REGISTRY` ships empty; each handler enters through its own issue citing ADR-0068 §6 |

### M — Feature identity for deck results (DG-M)

Registered by governance issue #453 after the DG-K2 closeout (#409); the design gate #454 produced ADR-0073, `ACCEPTED` on 2026-10-18 (#457), and governance issue #458 authorizes `M01`, `M02`, `M04` and `M05` for implementation on the integration branch `implement/fid`. The maintainer named the requirement on 2026-10-18: a deck result must be attributable to a server-known, canonical feature definition, the first being failed aggression. `SCOPE.md` (archived at the closeout #475) recorded the start gate and the acceptance of each atom. `M03` is superseded and not counted. `M01`, `M02`, `M04` and `M05` are `COMPLETE` on `implement/fid` within the bounds of their rows. Follow-on, not an atom: #477 / PR #478 lets the deck protocol client carry `definitions` (governance #479).

| ID | Capability | Owner | Requires | Unlocks | Decision | Impl | Acceptance / authority |
|---|---|---|---|---|---|---|---|
| M01 | Bar-flow representation v1 | Representation | D03, D06 | M02 | FROZEN | COMPLETE | ADR-0073 §2 (accepted; authorized by #458). Additive `BarFlowDefinitionV1(duration_ns)` and `bar-flow-v1` record: the candle's six aggregates plus `taker_buy_volume`/`taker_sell_volume`, one pass over `trade-v1`, no tick grid, `aggressor_side` missing or unknown refuses the bucket, duration 1 second to 1 day, identity domain `bar-flow-definition-v1`. The candle, footprint and H01 identities are pinned and must not change. Credited `COMPLETE` at the DG-M closeout (#475): #461 / PR #466 on `implement/fid`; `BarFlowDefinitionV1`, the `bar-flow-v1` record and its golden fixture; the candle, footprint and H01 identities are unchanged. |
| M02 | Failed-aggression FeatureDefinition v1 | Feature Engine | M01, E02, E06 | M04 | FROZEN | COMPLETE | ADR-0073 §1, §3, §4 (accepted; authorized by #458). `order_flow.failed_aggression@1`: input contract `bar_flow.bar@1`, FINAL_ONLY, exact division-free rule (`D >= i*T` and wick `> w*range`), defaults `imbalance_threshold` 0.3 and `wick_ratio` 0.5 with declared domains, signed output -1/0/+1; constructor, pure evaluator, golden vectors, and the reference `EventSpec` predicates (`equal 1`, `equal -1`, `not_equal 0`) exercised by a test. Credited `COMPLETE` at the DG-M closeout (#475): #462 / PR #468 on `implement/fid`; review findings R1-R3 fixed, R1 (observation identity across durations) held at admission level by ADR-0073 Amendment 1 (#469 / PR #471). |
| M03 | Failed-aggression event definition v1 | Research | M02, F02 | — | SUPERSEDED | N/A | Superseded by ADR-0073 §1 (accepted; closed by #458): an `EventSpec` is bound to a `hypothesis_id`, a research object, so no event definition is canonical or server-known. The reference-usage test moves into `M02`. Excluded from the inventory counts. |
| M04 | Server definition registry and admit carriage v1 | Application/API | M01, M02, S01, P06 | M05 | FROZEN | COMPLETE | ADR-0073 §5 (accepted; authorized by #458). Closed in-code registry (one entry); optional `definitions` field of `admit` (at most 8 entries, additive under ADR-0066 §1, family version unchanged); the manifest's `definition_identities` populated with the sorted, deduplicated bar-flow and feature identities; a request without `definitions` seals a byte-identical manifest; eight stable refusal reasons (ADR-0073 Amendment 1, #469: one bar duration per admission). Amendment 2 of ADR-0072 and Amendment 3 of ADR-0069 are appended by this atom. Credited `COMPLETE` at the DG-M closeout (#475): #463 / PR #473 on `implement/fid`; ADR-0072 Amendment 2 (§1 row) and ADR-0069 Amendment 3 appended; a request without `definitions` seals the pre-change digest; a wire digit ceiling found in review was removed. |
| M05 | Deck feature-identity declaration and proof v1 | Client/Operations | M04, S05, S07 | feature-traceable deck results | FROZEN | COMPLETE | ADR-0073 §6, §8 (accepted; authorized by #458). The deck builder no longer adds definition references itself and refuses `definition_not_declared`; K13 `_validate_run_compatibility` unchanged and now non-vacuous; the four facts of §8 proved on `PostgresSplitTopology`, with the omission case refused and zero writes asserted. Amendment 2 of ADR-0072 §5 is appended by this atom. The server attests the admitted name, not the deck's computation. Credited `COMPLETE` at the DG-M closeout (#475): #464 / PR #474 on `implement/fid`; ADR-0072 Amendment 2 (§5 item 2); the four facts of ADR-0073 §8 proved on `PostgresSplitTopology` (CI; also 18 of 18 run in WSL on PostgreSQL 16). The real-host run is operator evidence and is not claimed. |

### T — Multi-instrument Bybit linear trades (DG-N)

Registered and authorized by governance issue #483 after the design gate #377 produced ADR-0074, `ACCEPTED` on 2026-10-19 (PR #482). The maintainer wants to read, deliver and ingest live the Bybit linear USDT instruments BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT and DOGEUSDT, and to run this work in the current wave (`implement/fid`). `SCOPE.md` Part B records the start gate and the acceptance of each atom; the order is `T01`, `T04`, `T03`, `T02`. `T01` and `T04` are `COMPLETE` on `implement/fid` (reconciliation #489); `T03` and `T02` are not implemented.

| ID | Capability | Owner | Requires | Unlocks | Decision | Impl | Acceptance / authority |
|---|---|---|---|---|---|---|---|
| T01 | Multi-instrument read and delivery v1 | Data Access/Application | A06, A08, A09, K12 | T04, T03, T02 | FROZEN | COMPLETE | ADR-0074 §1, §2, §5 (accepted; authorized by #483). The closed constant `BYBIT_LINEAR_TRADE_INSTRUMENTS_V1` (BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT, DOGEUSDT) drives the ordering and eligibility applicability; a non-BTC dataset is read, admitted and delivered end to end (`DataGateway`, K12 `admit`/`deliver`); a read-only check of the catalog for the allow-list; what is missing enters the catalog only through the producer path (for the existing Parquet, by the archive import of ADR-0074 Amendment 1, not by registration); BTC identities and ordering key unchanged. Credited `COMPLETE` at the DG-N reconciliation (#489): #485 / PR #486 on `implement/fid`; the allow-list constant drives the ordering and eligibility applicability for all five instruments, each read through the `DataGateway` and admitted and delivered through K12 end to end on real Parquet and the J14 transfers, plus a read-only catalog check; mutation-checked; BTC identities and ordering key unchanged. The registration of the existing Parquet was superseded by ADR-0074 Amendment 1 (they are replaced through the archive import). Not claimed: that any non-BTC dataset is registered in the server catalog, a real-host run. |
| T04 | Historical import per instrument v1 | Source/Producer | A07, T01 | T02 | FROZEN | COMPLETE | ADR-0074 §4 and Amendment 1 (accepted; authorized by #483, re-scoped by #487). `--instrument` over the allow-list for the one-UTC-day historical import from the Bybit public trade archive (a local file, no network call in the producer path); history enters only through the producer path; no repair of live gaps (ADR-0044 stands). Credited `COMPLETE` at the DG-N reconciliation (#489): #487 / PR #488 on `implement/fid`; ADR-0074 Amendment 1 (accepted 2026-10-20) names the Bybit public trade archive as the historical source of every instrument; the archive adapter, the SQLite and archive certification profiles (no mixtures), the one-UTC-day import with `--instrument` through the producer path (S13, S14) under a per-day lock, the fetch, import and compare tools, the runbook and a PostgreSQL integration test in CI; BTCUSDT 2024-01-15 from the archive equals the SQLite golden on aggregates. Not claimed: the server-side record-by-record proof, a real-host run, any download or replacement of the existing files. |
| T03 | Explicit per-instrument fees v1 | Execution | H02, T01 | T02 | FROZEN | MISSING | ADR-0074 §7 (accepted; authorized by #483). Fee parameters are explicit per instrument; an execution or replay over a dataset whose instrument is not BTCUSDT and that names no fee schedule is refused (fail-closed); BTCUSDT behaviour unchanged. |
| T02 | Live trades ingest per instrument v1 | Data Plane/Operations | A11, K10, T01, T03, T04, O03 | multi-asset live research | FROZEN | MISSING | ADR-0074 §3, §6 (accepted; authorized by #483). Adapter and server parameterized by instrument; one process per instrument with its own checkpoint and subject (`quant-platform-live-ingest@<INSTRUMENT>.service`, runbook); two instances proved not to collide on checkpoints, partitions or catalog rows; one hour of real provider traffic for ETHUSDT (delivered by `O03`); deployment is a manual operator step and the capacity rule gates enabling beyond BTCUSDT. |

### O — Live publication: continuous session and append-only slices (DG-O)

Registered and authorized by governance issue #507 after the design gate #504 produced ADR-0075, `ACCEPTED` on 2026-10-21 (PR #505, #506). Measured on the maintainer's server on 2026-10-21: the live publication supersedes every cycle with the next (a day reads as under 0.4% of its published trades) and the loop closes its WebSocket between cycles (about 27% of a day's trades are never acquired). `SCOPE.md` Part C records the start gate and the acceptance of each atom; the order is `O01`, `O02`, `O03`. No atom here is implemented. `T02` requires `O03`: its one-hour `ETHUSDT` proof is the one of `O03`.

| ID | Capability | Owner | Requires | Unlocks | Decision | Impl | Acceptance / authority |
|---|---|---|---|---|---|---|---|
| O01 | Append-only live slices v1 | Data Plane/Application | A11, K10, T01 | O02 | FROZEN | MISSING | ADR-0075 §2 to §4 (accepted; authorized by #507). A slice is the trades of one instrument in a half-open exchange-time window, closed after `slice_seconds` (300) or `slice_max_trades` (50 000) at a message boundary and never crossing a UTC midnight; each slice is revision 1 of its own partition `dt=<day>/seg=<HHMMSS.mmm>` and is never superseded; windows of one session chain exactly; the checkpoint binds a slice partition; the live certification profile accepts the key grammar and checks the chain. Ends F1 (a day reads as its last cycle). |
| O02 | Continuous live session v1 | Data Plane/Operations | O01 | O03 | FROZEN | MISSING | ADR-0075 §1, §5 (accepted; authorized by #507). One WebSocket session per instance, decoupled from publication by a bounded buffer (20 000 messages); a buffer overflow, a disconnect or an ordering violation is an interruption handled by ADR-0040 §7 and ADR-0042 §5 (reconcile from the last durable key or record an explicit gap; ADR-0044 unchanged); signals for the buffer and the interruptions. Ends F2 (about 27% of a day's trades never acquired). |
| O03 | Live slice configuration, runbook and real-provider proof v1 | Data Plane/Operations | O02, T03, T04 | T02 | FROZEN | MISSING | ADR-0075 §9.3 (accepted; authorized by #507). `slice_seconds`, `slice_max_trades` and the buffer bound in the unit templates and the runbook; a real-provider run of at least one hour for ETHUSDT that reports the trades captured against the Bybit archive of the same day, the trades readable through the DataGateway and the number of `exchange_ts` ordering violations. This is the one-hour proof of `T02`. |

## Dependency integrity

The `Requires` relation is a DAG. In particular, operational capability and implementation sequencing are not conflated:

```text
A11 live acquisition -> K10 checkpoint/recovery implementation
K06 source protection -> K07 relocation
K06 source protection -> K08 backup/restore proof -> K09 deletion authority
```

K08/A11/K02/K10 are now complete for the selected first live-ingest path, including K10's bounded real-server restart proof (PR #122). Backup/restore does not require tier-relocation implementation.

## Decision gates

There are now **12 unresolved atom decisions** (3 `OPEN_BLOCKING`: `A13`, `I06`, `I07`; 9 `OPEN_DEFERABLE`: `A12`, `A15`, `B05`, `B08`, `C06`, `E07`, `H06`, `J11`, `S08`) grouped into gate families. The `DG-J` Wave 8 gates (`G1`-`G6`) are resolved and their nine atoms are implemented (Wave 8 closeout, issue #327); `J11` is deliberately `OPEN_DEFERABLE` under ADR-0065 §5. In addition, the selected Live Ingest scope carried one explicit scope-level DG-B proposition for long-gap remediation, now disposed (`NO_AUTHORITATIVE_REPAIR_PATH_PROVEN`, #110); it was never represented as a new atom and is not counted above.

A gate-family name is not a requirement to resolve every atom inside it. Selecting an atom activates only unresolved propositions on that atom's transitive dependency path.

### DG-A — Representation / Feature integration

`D05` candle-materialization identity: **semantic decision FROZEN** under
ADR-0059 (representation-artifact identity; candle support/availability;
replay-input-profile shape, including the non-interchangeability of tick and
representation replay absent a future declared bar-close profile).
Implementation remains `MISSING`: no runtime, materialization, or
representation-replay access exists. Activate implementation only when
persisted Candle results are actually selected.

The canonical H01 branch is **RESOLVED** under ADR-0035. `D06` FootprintDefinition v1 is frozen and complete under ADR-0027; `E02` FeatureDefinition v1 is frozen and complete under ADR-0026; `E04` FeatureArtifact v1 is frozen and complete under ADR-0034; E05 is complete; E06 semantic integration is frozen under ADR-0035 and implementation is `COMPLETE`.

`D05` is **not** a prerequisite of `E06`.

### DG-B — Historical / Live data convergence

Repair branch (`A16` + `A10` + `B04`) is frozen and complete for the accepted first vertical under ADR-0028/ADR-0029/ADR-0033.

A11 live-acquisition semantics are **RESOLVED / FROZEN** under ADR-0040 and the selected Bybit BTCUSDT runtime is now implementation `COMPLETE`. Historical/live cutover, canonical trade identity/order, duplicate conflict policy, bounded reconnect reconciliation and evidence-based coverage are no longer open A11 decisions.

One explicit scope-level proposition has been investigated and disposed:

- **long-gap remediation beyond bounded reconciliation — DISPOSED, `NO_AUTHORITATIVE_REPAIR_PATH_PROVEN` (#110, 2026-09-24).** If the last durable `TradeKeyV1` is unavailable from the bounded provider reconciliation evidence, the interval remains explicit non-complete coverage. A real Bybit historical archive was identified but could not be proven to satisfy exact-interval completeness or archive/recent-live overlap with the evidence available; the standing rule -- an attributable source/path must prove the missing support and integrate through the existing A10 repair semantics; missing `seq`, absence of trades or elapsed time are not completeness proof -- remains the accepted authority for any future attempt.

This disposition does not block A11/K10 from recording the gap and continuing with a new governed live segment; it means that interval stays explicitly non-complete rather than filled/lossless until real evidence exists (none does yet).

`B06` live-consumer cursor semantics are now `FROZEN` under ADR-0047 and implementation `COMPLETE` via PR #204, with bounded Golden coverage in PR #212. ADR-0040 remains acquisition authority; ADR-0047 is the consumer-cursor authority.

### DG-C — Market-data depth

Atom-specific branches:

- L1 branch: `A13` when a concrete L1 feed is selected;
- L2 branch: `A14` only when L2 is selected. L2 is selected (owner decision 2026-10-14, #364) and
  does **not** require `A13`; the earlier "after A13" ordering is revised. **RESOLVED by ADR-0070 (#367).**

L1 does not activate L2. L3/MBO remains deferable until real L3 evidence exists.

The L2 branch was registered by #364 as atoms `A14a`-`A14f` in two batches: `v1` (historical ingest and
consumer read) and `v2` (live ingest), and resolved by ADR-0070 (#367), which also registers the follow-on
`A14g` (`ob500` projection). Governance #442 closes batch v1 on `implement/l2`: `A14` and
`A14a`-`A14e` are COMPLETE, golden #413/#430 accepted (11/11 at `1cdd78f`).
`A14f`/`A14g` stay held under the unchanged `SCOPE.md` Part B gates. The L2 line remains cut
from `implement/dgk`; promotion to `main` is a separate wave closeout PR.

PR #317 / issue #316 (operator-requested emergency slice on `implement/wave-8`)
added an `l2-book-event-v1` schema, `L2BookEvent` identity and pure venue
adapters. This was groundwork only. ADR-0070 ratifies `l2-book-event-v1` unchanged
(the schema bytes do not change; the `exchange_ts` mapping of the normalizer is corrected by
`A14a`). The completed v1 acquisition/publication/materialization/read path is bounded by
ADR-0070 Amendments 1-2: UTC-day ownership by `exchange_ts`, per-run partitions and explicit
source-file gaps. ADR-0049 Amendment 1 (#423 / PR #424) permits one redundant physical source
copy to be disposed only while an independent verified copy remains; the last copy is never
removable. #425 / PR #426 is credited as an operational tool, not an A14 atom or new K09 credit.
The [eight reopening triggers](../architecture/OPEN_DECISIONS.md#l2-v1-reopening-triggers-442)
and [Not claimed](../../SCOPE.md#not-claimed) are binding limits; none authorizes follow-on work.

### DG-D — Application configuration — RESOLVED

C05 is frozen: executable owns CLI/environment acquisition; precedence is explicit CLI > environment > declared default > failure; Application receives typed resolved configuration and owns composition; no generic DI/service-locator framework.

### DG-E — Validation semantics — RESOLVED / IMPLEMENTED

F07 is frozen/complete under ADR-0036 and F08 is frozen/complete under ADR-0037. DG-E has no remaining open branch through F08.

### DG-F — Strategy / Execution semantics — RESOLVED / IMPLEMENTED

`G04` session/cooldown semantics are frozen by ADR-0045 and implementation complete. `H03` execution-conflict / intra-bar fill semantics are frozen by ADR-0046 and implementation complete. The full `G01`-`G04` / `H01`-`H05` Strategy / Replay path is integrated on `implement/wave-4`, and PR #161 / issue #146 records Golden V7 deterministic replay PASS.

### DG-G — Experiment / RL / Jobs

The supervised Experiment / ML branch is resolved and implementation-complete
through I05: I03 is integrated by PR #178 / issue #171, I04 by PR #177 / issue
#172, I05 by PR #179 / issue #173, and PR #180 / issue #174 records the Wave 5
Golden E2E supervised proof with stable projection, run, metric and artifact
identities. `J03`'s durable job-runtime decision is `FROZEN` under ADR-0062
and its durable record/lifecycle is implementation `COMPLETE` (PR #307,
Wave 8); dispatch and a worker process are not claimed and belong to DG-K.
Remaining open DG-G atom decisions are only `I06` and `I07`.

### DG-H — Operational safety

K02, K03, K05, K06, K08 and K10 are complete for the selected first Live Ingest path:

- K02 — ADR-0041, `COMPLETE` via PR #113 real-server identity/ACL/database/storage proof;
- K08 — ADR-0039, `COMPLETE` via PR #111 plus PR #113 deployment-independence proof;
- K10 — ADR-0042, `COMPLETE` via PR #114 (hermetic implementation/proof matrix) plus PR #122 (bounded real-server restart/deployed checkpoint proof, issues #109/#121).

K07 tier relocation and K09 deletion authority are now `FROZEN` under ADR-0048/ADR-0049 and implementation `COMPLETE` via PR #209/#211, with bounded Golden coverage in PR #212. DG-H has no currently identified open atom after Wave 6 closeout.

### DG-I — API transport / client convergence — RESOLVED / IMPLEMENTED

`J02` canonical API transport is resolved by ADR-0050 and implementation
`COMPLETE` in Wave 7. `J04` CLI, `J05` TUI and `J06` App UI are also
implementation `COMPLETE` as thin clients over the accepted API/client
boundary. Issue #222 was closed by explicit operator exception during expedited
Wave 7 closeout, so this section does not claim an additional dedicated Golden
E2E artifact beyond the merged slice evidence.

ADR-0050 Amendment 1 froze J02's result-size bound and wire `max_size`; it left
authentication/TLS for non-loopback deployment explicitly open. That gap is
frozen under ADR-0063 and implemented by `J09` (PR #310, Wave 8) — see `DG-J`
G1 below.

### DG-J — Remote Service Topology (Wave 8 design gates) — RESOLVED / IMPLEMENTED

Gate family for `SCOPE.md`'s "Wave 8 — Full Platform API & Remote Service
Topology v1". All six gates reached a disposition on 2026-10-04 (issue #288,
parent #281):

- **G1** -> `J09` **FROZEN** (ADR-0063: WSS+mTLS, exact-fingerprint principal mapping, `j02.market_data.read` scope);
- **G2** -> `J03` **FROZEN** (ADR-0062: durable admission/lifecycle/retry/result semantics for a server-local Job);
- **G3** -> `K12`, `K13` **FROZEN** (ADR-0064: the deck is the sole consumer surface — no third machine; sealed admitted-input manifest; exact result-import refusal/idempotency);
- **G4** -> `J10`, `J12`, `J13` **FROZEN** (ADR-0065 §2-§4: Strategy/Validation/Training seams); `J11` **OPEN_DEFERABLE** (ADR-0065 §5: Replay seam explicitly deferred — no accepted `feature_provider` identity yet);
- **G5** -> `J14` **FROZEN** (ADR-0066: additive finite chunked-result framing; no live family defined);
- **G6** -> `J15` **FROZEN** (ADR-0067: J02 is the sole remote boundary; implementation explicitly blocked until `J09` is implemented).

**Implementation:** all nine atoms are implemented and on `main` (Wave 8
closeout, issue #327; PR #307-#312, #315, #320, #321; promoted by PR #322).
Each atom's row above states exactly what is credited. `implement/omega`,
`implement/wave-8` and `implement/wave-8.1` are closed integration branches.
J02 carriage of `J10`/`J12`/`J13`, J03 dispatch/worker and a single runtime
store are carried to DG-K Process Topology v1 (#318/#319), not to this gate.

### DG-K — Process Topology v1 — RESOLVED (ADR-0068, ADR-0069)

Registered by governance issue #318 after the Wave 8 closeout and resolved by
design gate #319: ADR-0068 (process topology v1) freezes `P01`-`P04b` and
ADR-0069 (J02 carriage of consumer seams and deck handoff v1) freezes `P05a`,
`P05b` and `P06`. Closed by operator closeout #381: all eight atoms are `COMPLETE` within
the bounds recorded in their rows, merged into `implement/dgk`; `SCOPE.md` records the
result and what is not claimed. Fixed owner inputs: identity-preserving canonical profiles (P01), hosts
in `tools/` (P03), one J03 worker (P04), P05 in DG-K, a free server with
data-local J03 handlers only and heavy compute on the deck on its own
initiative, and `J11` not planned. Full question set and stop conditions:
`OPEN_DECISIONS.md` (DG-K).

```text
P01 ──┐
P02 ──┼─> P04a ─> P04b ─> P05b
P03 ──┘     (P04b also requires P03)
P05a       (requires J02/J09 only)
P06        (requires J09, J14, P02)
```

### DG-K2 — Server/deck deployment composition v1 — CLOSED (operator closeout #409)

Closed by operator closeout #409: `S01`-`S07` are `COMPLETE` within the bounds recorded in their rows, merged into `implement/dgk2`; `SCOPE.md` records the result and what is not claimed. Registered by governance issue #382 after the DG-K closeout (#381). DG-K delivered the families, stores, the worker host and the deck protocol client but no executable composes them and nothing proves them on the real topology. The gate is one ADR (next free number) that decides the K12 server authority, the K13 composition, the API host configuration, credential and trust operations, the deck-side bundle producer, deployment units and the definition of the real-topology proof. Fixed inputs (not reopened): ADR-0057, ADR-0063 (no credential service), ADR-0064, ADR-0068, ADR-0069 and its amendments, `J11` not planned. Omega adoption (ADR-0067 amendment) is out of scope unless the maintainer adds it. Full question set, valid closes and stop conditions: `OPEN_DECISIONS.md` (DG-K2).

```text
S01 ──┐
S02 ──┼─> S03 ─> S06 ─┐
S04 ──┘               ├─> S07
S05 ──────────────────┘
S08   (deferable; trigger: a named first handler)
```

### DG-M — Canonical feature identity for deck results v1 — CLOSED (design gate; ADR-0073 `ACCEPTED`, #457)

Closed by ADR-0073: one canonical feature `order_flow.failed_aggression@1` over a new additive `bar-flow-v1` representation; the event is not server-known (`M03` superseded); the deck names definitions in an optional `definitions` field of `admit` and the server seals their identities in `definition_identities`; K13 is unchanged and the deck builder must declare them. Implementation of `M01`, `M02`, `M04`, `M05` was authorized by #458 and is `COMPLETE` on `implement/fid` (closeout #475). Registered by governance issue #453 after the DG-K2 closeout (#409), whose trigger 1 ("the maintainer names feature traceability as a requirement") the maintainer exercised on 2026-10-18. The server passes the deck no feature identity (`definition_identities` is empty), no governed representation carries taker volumes, and the failed-aggression observable has no canonical definition. The gate is one ADR (ADR-0073) that decides the decomposition, the input representation, availability, numerics, the server carriage, the K13 rule, the research alignment and the proof. Fixed inputs (not reopened): ADR-0057, ADR-0064, ADR-0066 §1, ADR-0069, ADR-0072, ADR-0026/ADR-0035, no change of any existing identity bytes. Out of scope: the generic provider `E07` and the FEI line (DG-L, independent), precomputed feature artifacts as deck inputs, Omega, `J11`. Full question set: `OPEN_DECISIONS.md` (DG-M).

```text
M01 ──> M02 ──> M04 ──> M05      (M03 superseded)
```

### DG-N — Multi-instrument Bybit linear trades v1 — CLOSED (design gate; ADR-0074 `ACCEPTED`, #377)

Closed by ADR-0074: a closed allow-list of five Bybit linear USDT instruments, per-instrument dataset, subject and checkpoint identity, one live process per instrument, ADR-0044 unchanged for gaps, a capacity rule that gates production enabling on the #378 measurements, explicit fees per instrument. Implementation of `T01`-`T04` was authorized by #483; `T01` and `T04` are `COMPLETE` (#489) and `T03` and `T02` are not started. ADR-0074 Amendment 1 (accepted 2026-10-20, #487) chose the Bybit public trade archive as the historical source. Fixed inputs (not reopened): ADR-0040, ADR-0042, ADR-0043, ADR-0044, no change of any BTCUSDT identity byte. Out of scope: a second venue (`A12`), spot and inverse, L2 for other instruments, a generic provider resolver, measured capacity.

```text
T01 ──> T04 ──> T03 ──> T02
```

### DG-O — Live publication: continuous session and append-only slices v1 — CLOSED (design gate; ADR-0075 `ACCEPTED`, #504)

Closed by ADR-0075: one continuous WebSocket session per instance decoupled from publication by a bounded buffer; slices with chained exchange-time windows (300 s or 50 000 trades); each slice is revision 1 of its own partition `dt=<day>/seg=<start>`, never superseded; interruptions follow ADR-0040 §7 and ADR-0042 §5, ADR-0044 unchanged. Implementation of `O01`-`O03` is authorized by #507 and not started. Not decided (deferred, with a measured trigger): consolidation of a closed day's slices into one partition. Not repaired (paused by the maintainer): the already damaged live days, which stay as they are; if a gap makes the data unusable the history is re-ingested from scratch. Assumption to be measured by `O03`: the provider's `exchange_ts` is non-decreasing along the stream. Out of scope: a second venue (`A12`), L2, any change to `trade-v1`, the ordering key, `TradeKeyV1` or the J02 wire.

```text
O01 ──> O02 ──> O03 ──> T02
```

## Vertical milestones

| ID | Path | Proposition | State |
|---|---|---|---|
| V1 | Source -> canonical -> catalog -> DataGateway | First published data vertical is deterministically readable through canonical access | COMPLETE |
| V2 | DataGateway -> Application | Semantic historical request reaches canonical bounded data without caller storage identity | COMPLETE (`C02 + C03`) |
| V3 | DataGateway -> historical Candle | Bounded trades yield reproducible CLOSED candles | COMPLETE (`D03`) |
| V4 | Representation -> Feature -> H01 | Canonical footprint + FeatureDefinition/Artifact produce H01 with provenance | COMPLETE (`E06`; ADR-0035) |
| V5 | Feature -> Research | Feature artifacts produce reproducible Event/Outcome studies and sweeps | COMPLETE (`F02`/`F03`/`F04`; F03 authority ADR-0038) |
| V6 | Research -> Validation | Populations are leakage-safe and labels/censoring are explicit | COMPLETE (`F06` + `F07`) |
| V7 | Strategy -> Replay | Decision/risk becomes deterministic orders/fills/portfolio replay | COMPLETE (`G01`-`G04` + `H01`-`H05`; ADR-0045/ADR-0046; PR #161/#146 Golden PASS) |
| V8 | Historical -> Live | Historical and live paths converge under repair/coverage/cursor/recovery/storage guarantees | COMPLETE for bounded data-plane/storage lifecycle path under ADR-0043/ADR-0044/ADR-0047/ADR-0048/ADR-0049 and PR #212; broader live product gates remain open |
| V9 | Application -> API -> Client | One application semantic implementation serves thin clients | COMPLETE for bounded WebSocket transport and thin clients; #222 exceptional Golden waiver |
| V10 | Paper -> Live | Full canonical stack crosses explicit operational authorization gate | BLOCKED |

## Execution frontier

Wave 7 API & Platform Transport is closed on `implement/wave-7`: ADR-0050 and
PR #225/#226/#227/#228 complete J02/J04/J05/J06. Issue #222 was closed by
explicit operator exception during expedited closeout, so this document does
not claim an additional dedicated Golden E2E artifact.

The post-Wave-7 `implement/omega` stabilization line (tracking #232) is now
reconciled: eight design gates (ADR-0054-0061) and nine implementation/
hardening slices merged across PR #254, #256-#278, plus amendments to
ADR-0037 and ADR-0050. It resolves `D05`'s semantics (still `MISSING`
implementation) and closes several audit findings; it does not select a new
runtime/product frontier by itself.

Wave 8 — Full Platform API & Remote Service Topology v1 is closed by operator
closeout (issue #327): `J03`, `J09`, `J10`, `J12`, `J13`, `J14`, `J15`, `K12`
and `K13` are implemented and promoted to `main` (PR #322, #326), within the
bounds recorded in their rows. Owner-accepted extras #313 (replay ledger
identity cost) and #314 (uniqueness weights) are also on `main` via the ad-hoc
`implement/wave-8.1` branch, with identities unchanged. No Wave 8 Golden E2E
proof artifact is claimed. `J11` remains `OPEN_DEFERABLE`.

DG-K P01-P06 (#381) and DG-K2 S01-S07 (#409) are COMPLETE within the
bounds in their rows and promoted into main (#434/#437). DG-K delivered the
libraries, wire families, PostgreSQL runtime, one worker and the deck protocol
client; DG-K2 subsequently supplied the opt-in API host composition, authority
seams, output registry, credential/runbook tooling and deck workload module.
Its supervised proof is one happy path on the smallest real partition; it does
not claim a largest-day, concurrent, real-network or fault-injection proof on
the real host. The empty handler registry and held S08 remain unchanged.

L2 v1 is CLOSED on implement/l2 (#442/#443): A14 and A14a-A14e COMPLETE,
golden #413/#430 accepted 11/11 with explicit gaps, under ADR-0070 Amendments 1-2.
Reconciliation #444 combines the two histories before the separate final L2
promotion. A14f/A14g remain held; no live L2, ob500, population guarantee or
gap-free UTC-midnight L2 is claimed. All eight reopening triggers remain
NOT AUTHORIZED; SCOPE.md preserves both closeouts and their limits.

No new runtime/product frontier is authorized by this document. Neither the
Wave 7 closeout, the Omega stabilization line, the `DG-J` resolution nor the
Wave 8 closeout activates H06, I06-I07, J07/J08, RL, live broker execution, second venue or
L1/L3 or L2 beyond the bounded historical v1 credit above. J07 remains `MISSING`.

This is a **frontier, not concurrent authorization**. `SCOPE.md` records the
closed scope, the authorized implementation inventory, and its exclusions.

## Live Ingest Server Production Readiness v1 — derived governance map

`SCOPE.md`'s "Governance Map Generation Requirements" section required a
derived execution map complete enough to generate the next issues without
re-litigating the scope boundary. This section is the completed temporary map:
it generated issues #126, #127, #128, #135 and #129 and is retired by this
closeout. It does not add permanent DAG atoms.

**Credited state (reuse, do not reimplement):** K08, A11, K02, K10, A10, B04,
K03, K04, K05, K06 — all `COMPLETE` above and in `SCOPE.md`'s Credited State.

**Reconciled stale governance state:** the K10 `PARTIAL`/
`REAL_RESTART_PROOF_PENDING` phrasing across this file, `CAPABILITY_MAP.md`,
`ROADMAP.md` and `OPEN_DECISIONS.md` is reconciled to `COMPLETE` with PR #122
evidence. No open action remains here.

**Production-readiness blockers** (`SCOPE.md` P1-P6): resolved or disposed for
the selected first producer operating path. P1/P6 are closed by ADR-0043,
`quant_platform.application.live_ingest_server`, `tools/live_ingest_server.py`
and the supervised deployment runbook. P2/P3/P4 are closed by ADR-0044's
explicit-gap-only, fail-closed state path: no authoritative long-gap repair
source is claimed, and unresolved gaps remain non-complete. P5 is preserved by
the server loop, long-gap orchestration tests and target proof.

**Design gates** (must resolve before any code change):

| Gate | Resolves | Authority | Artifact | Acceptance evidence |
|---|---|---|---|---|
| Active Path step 2 — ingest server v1 design | P1, loop classification (P7) | `SCOPE.md` step 2; ADR-0041; existing `application.bybit_live` entrypoints | ADR-0043 | DONE; bounded server owner/entrypoint selected without generic scheduler/J03/J08 |
| Active Path step 3 — long-gap remediation design | P2, P3, P4 | `SCOPE.md` step 3; ADR-0033; ADR-0040/ADR-0042; issue #110 disposition | ADR-0044 | DONE; explicit-gap-only behavior retained unless future attributable repair evidence exists |

**Implementation atoms** (bounded issues, blocked on their design gate):

| Slice | Resolves | Blocked on | Authority | Artifact | Acceptance evidence |
|---|---|---|---|---|---|
| Active Path step 4 — bounded ingest server loop | P1, P5, P6 | step 2 | ADR-0043; K02/A11/K10/A10/K03/K05/K06 | `quant_platform.application.live_ingest_server`; `tools/live_ingest_server.py` | DONE; publication-before-checkpoint preserved; operator evidence observable |
| Active Path step 5 — long-gap state and repair orchestration | P2, P3, P4, P5 | step 3 | ADR-0044; A10/A16 repair seams | `quant_platform.application.live_gap_orchestration` | DONE; gap stays explicit until repair evidence passes; no state silently claims completeness |

**Real-server proof atom:** DONE. Active Path step 6 evidence is recorded in
`docs/integration/LIPR05_LIVE_INGEST_SERVER_PRODUCTION_READINESS_PROOF.md` and
`docs/integration/LIPR06A_SUPERVISED_LIVE_INGEST_DEPLOYMENT.md`, including
target supervised start, clean stop, restart, bounded reconciliation,
checkpoint advancement and DataGateway readback PASS.

**Closeout atom:** DONE. Active Path step 7 reconciles `CAPABILITY_MAP.md`,
this file, `ROADMAP.md`, `OPEN_DECISIONS.md` and `SCOPE.md` to the strongest
actually-proven propositions while preserving explicit exclusions.

**Historical explicit exclusions for this retired Live Ingest map** (must not
be read as implicit issue work inside that scope): B06, K07, K09, J08,
paper/shadow mode, Strategy/Replay/Execution/Portfolio/ML/RL, J02
API/client work, L1/L2/L3/MBO, second venue/generic provider resolution,
generic job scheduler/broker/workflow engine, HA/distributed consensus/
off-site DR, and speculative long-gap filling without attributable source
evidence. `SCOPE.md`'s Out Of Scope list is authoritative; no issue derived
from this map may pull these in without a concrete acceptance blocker.
Wave 6 later selected and completed B06/K07/K09 under its own dedicated scope
and ADRs; this retired map did not authorize them.

## Execution waves

Waves are dependency/value groupings and differ from temporary `implement/wave-*` git integration branches.

```text
Wave 0  Architecture foundation                           COMPLETE
Wave 1  First canonical computation/application slices    COMPLETE (C05,D03,E05,F05,I01,K04)
Wave 2  Representation / Feature vertical                 COMPLETE (E04,E06; ADR-0034/ADR-0035)
Wave 3  Research / Validation                             COMPLETE (F01-F08; ADR-0031/ADR-0036/ADR-0037/ADR-0038)
Wave 4  Strategy / Replay                                 COMPLETE (G01-G04,H01-H05; V7 Golden PASS)
Wave 5  Experiment / Supervised ML                       COMPLETE (I01-I05; Golden supervised E2E PASS)
Wave 6  Live Data Plane                                   COMPLETE (B06,D04,K07,K09 plus credited K02,K03,K04,K05,K06,K08,K10,A10,A11,A16; Golden PASS)
Wave 7  Runtime / Clients                                 COMPLETE (J02 -> J04/J05/J06; exceptional Golden waiver)
Wave 8  Full Platform API & Remote Service Topology v1    COMPLETE (J03,J09,J10,J12,J13,J14,J15,K12,K13 within
                                                           recorded bounds; J10/J12/J13 in-process only; J11
                                                           deferred; no Golden proof claimed; operator closeout #327)
DG-K   Process Topology v1                               COMPLETE within recorded bounds (operator closeout #381); P01-P06;
                                                           not composed in a host, no Golden proof claimed; before Wave 9
DG-K2  Server/deck deployment composition              COMPLETE within recorded bounds (operator closeout #409); S01-S07; deployable only within ADR-0072 Amendment 1 bounds; S08 not authorized
DG-C/L2 L2 historical v1 (#442)                         COMPLETE on implement/l2: A14,A14a-A14e; golden #413/#430 11/11;
                                                       A14f/A14g held MISSING; separate L2 promotion to main
DG-M   Canonical feature identity for deck results v1   COMPLETE within recorded bounds on implement/fid (closeout #475); M01,M02,M04,M05; M03 superseded; no real-host run claimed
DG-N   Multi-instrument Bybit linear trades v1          IN PROGRESS on implement/fid (#483, #489; ADR-0074 ACCEPTED #377 and Amendment 1); T01,T04 COMPLETE; T03,T02 FROZEN/MISSING
DG-O   Live publication: continuous session and append-only slices v1   AUTHORIZED (#507; ADR-0075 ACCEPTED #504); O01,O02,O03 FROZEN/MISSING; nothing implemented
Wave 9  Reinforcement Learning                            I06 strategic RL, I07 execution RL
Wave 10 Paper / Live product                              J07 then J08 after required data/execution/ops gates
```

See `ROADMAP.md`'s "Post-Wave-7 sequencing" for the explicit priority rationale
(usability of existing capabilities first); this is a priority order, not
concurrent authorization for Wave 9 or Wave 10.

Parallelism is allowed whenever DAG dependencies are satisfied. The closed Live Ingest scope nevertheless preserves the repository rule that governed it: one bounded mutation slice at a time.
