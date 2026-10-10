# Quant Platform Capability DAG vNext

Status: **CANONICAL; Wave 8 — Full Platform API & Remote Service Topology v1 CLOSED by operator closeout (issue #327): `J03`, `J09`, `J10`, `J12`-`J15`, `K12`, `K13` implemented and promoted to `main` (PR #322, #326), within the bounds recorded in their rows; no Wave 8 Golden proof claimed; `J11` remains `OPEN_DEFERABLE` (not planned, owner decision 2026-10-10). DG-K Process Topology v1 is resolved by ADR-0068 and ADR-0069 (#319); atoms `P01`-`P06` are `FROZEN`/`MISSING` and authorized in `SCOPE.md` Part B, with no implementation issue opened yet.**

This document is the execution/dependency view of the Quant Platform roadmap. `ROADMAP.md` remains the human-readable macro progression; `CAPABILITY_MAP.md` remains the compact current-state view; accepted ADRs/contracts remain semantic authority.

## Planning model

Roadmap readiness is not the same thing as freezing every future semantic choice.

An atom is roadmap-defined when its observable outcome, owner, dependencies, unlocks, acceptance proposition and decision state are classified, and any unresolved decision is either assigned to an atom-specific blocking gate or has an explicit deferable evidence trigger.

Current audited inventory after the DG-K design-gate reconciliation (#319;
recomputed mechanically from the atom tables below, not by hand):

```text
TOTAL_ATOMS                     = 104
CLASSIFIED_ATOMS                = 104
UNCLASSIFIED_GAPS               = 0
SEMANTIC_FROZEN_OR_RESOLVED     = 92 / 104 = 88.5%
OPEN_BLOCKING                   = 4
OPEN_DEFERABLE                  = 8
ROADMAP_DEFINED                 = 92 + 4 + 8 = 104
ROADMAP_PLANNING_COMPLETENESS   = 104 / 104 = 100%
IMPLEMENTATION_COMPLETE         = 78 / 104 = 75.0%
```

DG-K design-gate reconciliation (#319): ADR-0068 and ADR-0069 are accepted, so
the eight `P` atoms move from `OPEN_BLOCKING` to `FROZEN` (84 -> 92 decided).
Implementation stays `MISSING`; `IMPLEMENTATION_COMPLETE` is unchanged.

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
| A13 | L1 contract/acquisition | Data Plane | A01,A04,real feed | A14 | OPEN_BLOCKING | MISSING | Versioned schema/ordering/provenance; DG-C L1 branch |
| A14 | L2 contract/acquisition | Data Plane | A13,real feed | A15 | OPEN_BLOCKING | MISSING | Snapshot/increment/gap semantics; DG-C L2 branch |
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
| J03 | Job runtime | Runtime | C03,I02 | long operations | FROZEN | COMPLETE | ADR-0062 durable admission record, deterministic `job_id`, §3 lifecycle, attempt evidence, no automatic retry and restart -> `RECOVERY_REQUIRED` implemented by PR #307 / issue #296 (`application/durable_jobs.py`, SQLite store). Handler dispatch, worker process and PostgreSQL store are not claimed (DG-K P02/P04) |
| J04 | CLI client | Client Layer | J02 or explicitly bounded in-process C03 | operator workflow | RESOLVED | COMPLETE | Thin client; no quantitative/storage logic |
| J05 | TUI client | Client Layer | J02 | interactive workflow | RESOLVED | COMPLETE | Same canonical semantics |
| J06 | App UI | Client Layer | J02 | product workflow | RESOLVED | COMPLETE | Business logic remains behind service boundary |
| J07 | Paper/shadow mode | Runtime | A11,H05,I05,J02,K03 | J08 | RESOLVED | MISSING | Same decisions, simulated routing, explicit evidence |
| J08 | Live product mode | Runtime/Operations | J07,K02,K03,K05,K06,K08,K09,K10 | production | RESOLVED | MISSING | Explicit authorization/audit/recovery gates |
| J09 | Authenticated/TLS transport v1 | API Runtime | J02 | K12,K13,J10-J13,J15 | FROZEN | COMPLETE | ADR-0063 WSS+TLS 1.3+mTLS non-loopback J02, exact-fingerprint principal mapping, `j02.market_data.read` scope and security evidence implemented by PR #310 / issue #297. Only `j02.market_data.read` exists |
| J10 | Strategy Consumer-API seam v1 | Application/API | C03,G01-G04,J02 | J04-J06 extension | FROZEN | COMPLETE | ADR-0065 §2 `strategy-compose-v1` implemented as an in-process application seam by PR #308 / issue #300. Not reachable over J02 (no wire message or remote scope; owner decision 2026-10-08 assigns that to DG-K P05) |
| J11 | Replay Consumer-API seam v1 | Application/API | C03,H05,J02 | J04-J06 extension | OPEN_DEFERABLE | MISSING | ADR-0065 §5 explicitly defers `DG-J` G4's Replay seam: `HistoricalReplayRuntime.run()`'s injected `feature_provider` callable has no accepted identity-bearing, serializable, server-resolved definition yet. Owner decision 2026-10-10 (DG-K, #318): not planned; replay runs only on the deck over K12-admitted inputs, results return through K13 (P06). Trigger: an explicit maintainer request for server-side or remote-client replay, then a future ADR defining that feature/provider reference and whether the operation is synchronous or J03-admitted |
| J12 | Validation Consumer-API seam v1 | Application/API | C03,F06-F08,J02 | J04-J06 extension | FROZEN | COMPLETE | ADR-0065 §3 and Amendment 1 finite Validation seam (folds, classification, DSR, bounded synchronous PBO) implemented in-process by PR #312 / issue #301. Not reachable over J02 (DG-K P05) |
| J13 | Training Consumer-API seam v1 | Application/API | C03,I03-I05,J02 | J04-J06 extension | FROZEN | COMPLETE | ADR-0065 §4 and Amendment 2 bounded `supervised-train-evaluate-v1` seam implemented in-process by PR #315 / issue #302, including RunIdentity/projection provenance refusal. Not reachable over J02 (DG-K P05) |
| J14 | Streaming/live transport extension v1 | API Runtime | J02 | future live consumer seams | FROZEN | COMPLETE | ADR-0066 `j14-framed-result-v1` framing (RFC 8785, per-chunk/transfer SHA-256, transfer-local resume, bounded retention) implemented by PR #320 / issue #303 (`application/framed_result_transport.py`). Not yet dispatched by the J02 server; no live family |
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
| K12 | Server-owned admitted-input export/reference v1 | Operations | K02,B01-B04 | deck/consumer-machine compute | FROZEN | COMPLETE | ADR-0064 §2 sealed `AdmittedInputManifestV1`, `admission_id` and `ADMITTED`..`DELIVERED` state machine implemented by PR #309 / issue #298 (`application/admitted_input.py`, SQLite store). Physical byte transfer to the deck and a deck-side reader are not claimed |
| K13 | Governed experiment-result import/registration v1 | Operations | K12,I01,I02 | attributable deck results | FROZEN | COMPLETE | ADR-0064 §3 full-bundle refusal, idempotent re-submission and distinct identities implemented by PR #311 / issue #299 (`application/governed_result_import.py`). Experiment write (PostgreSQL) and evidence record (SQLite) are not one transaction; DG-K P02 must establish it |

### P — Process Topology (DG-K; ADR-0068, ADR-0069)

Registered by governance issue #318 and frozen by the design gate #319
(ADR-0068 process topology v1, ADR-0069 J02 carriage of consumer seams and
deck handoff v1), which kept the registered names. `SCOPE.md` Part B
authorizes them; no implementation issue is open yet.

| ID | Capability | Owner | Requires | Unlocks | Decision | Impl | Acceptance / authority |
|---|---|---|---|---|---|---|---|
| P01 | Canonical identity serializer v1 | Engineering/Application | — | P04a, identities computed in several processes | FROZEN | MISSING | ADR-0068 §5. One module with named, versioned byte profiles; every existing identity site declares its profile; golden-hash parity on every existing identity vector (including #313's replay vectors); no identity changes. RFC 8785 is one profile, for J14 and new contracts only |
| P02 | Runtime store on PostgreSQL v1 | Operations/Application | J03,K12,K13 | P04a,P06 | FROZEN | MISSING | ADR-0068 §4. `runtime` schema and DB role for the J03/K12/K13 stores (today SQLite); establishes atomic K13 registration with the Experiment write (today PostgreSQL then SQLite, `application/governed_result_import.py:268-288`) |
| P03 | Executable host boundaries v1 | Engineering | — | P04b | FROZEN | MISSING | ADR-0068 §7. Hosts stay in `tools/` (owner decision; ADR-0043 unchanged); per-host import rules in `tests/test_package_boundaries_v1.py`; `clients/*` stay outside `tools/` and `src/quant_platform` |
| P04a | J03 handler registry and dispatch v1 | Application | P01,P02 | P04b | FROZEN | MISSING | ADR-0068 §6. Closed, declared, data-local handler set inside `quant_platform.application` (owner decision: no replay/sweep/training handlers on the server); no generic executor (ADR-0062 §6) |
| P04b | Single J03 worker process v1 | Runtime | P03,P04a | P05b | FROZEN | MISSING | ADR-0068 §6, §8. Exactly one worker process over the PostgreSQL-backed queue; no broker; restart recovery stays correct with one worker; evidence trigger recorded for more than one (ADR-0062 §3/§4) |
| P05a | J02 carriage of synchronous J10/J12/J13 v1 | API Runtime | J02,J09 | J04-J06 extension | FROZEN | MISSING | ADR-0069 §1-§3. J02 wire messages and one remote scope per operation family (ADR-0063) without changing ADR-0065 semantics or J02 v1; J13 requests respect the 16 MiB bound (ADR-0050 §2) |
| P05b | J02 submit/status/result for J03 data-local jobs v1 | API Runtime | P04b | remote data-local jobs | FROZEN | MISSING | ADR-0069 §4. Submit/status/result messages for data-local operations admitted through J03 |
| P06 | Deck handoff over J02 v1 | API Runtime/Operations | J09,J14,P02 | deck replay/training with governed results | FROZEN | MISSING | ADR-0069 §5-§8. K12 delivery of admitted input bytes (J14 framing for large payloads) and K13 result-bundle submission, one least-privilege scope each; deck-side client verifies delivered digests and submits the bundle; the K13 bundle carries the replay feature-provider code identity. ADR-0064 §4 defines no wire shape today |

## Dependency integrity

The `Requires` relation is a DAG. In particular, operational capability and implementation sequencing are not conflated:

```text
A11 live acquisition -> K10 checkpoint/recovery implementation
K06 source protection -> K07 relocation
K06 source protection -> K08 backup/restore proof -> K09 deletion authority
```

K08/A11/K02/K10 are now complete for the selected first live-ingest path, including K10's bounded real-server restart proof (PR #122). Backup/restore does not require tier-relocation implementation.

## Decision gates

There are now **12 unresolved atom decisions** (4 `OPEN_BLOCKING`: `A13`, `A14`, `I06`, `I07`; 8 `OPEN_DEFERABLE`: `A12`, `A15`, `B05`, `B08`, `C06`, `E07`, `H06`, `J11`) grouped into gate families. The `DG-J` Wave 8 gates (`G1`-`G6`) are resolved and their nine atoms are implemented (Wave 8 closeout, issue #327); `J11` is deliberately `OPEN_DEFERABLE` under ADR-0065 §5. In addition, the selected Live Ingest scope carried one explicit scope-level DG-B proposition for long-gap remediation, now disposed (`NO_AUTHORITATIVE_REPAIR_PATH_PROVEN`, #110); it was never represented as a new atom and is not counted above.

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
- L2 branch: `A14` only when L2 is selected, after A13.

L1 does not activate L2. L3/MBO remains deferable until real L3 evidence exists.

PR #317 / issue #316 (operator-requested emergency slice on `implement/wave-8`)
added an `l2-book-event-v1` schema, `L2BookEvent` identity and pure venue
adapters. This is groundwork only: `A14` stays `OPEN_BLOCKING`/`MISSING`
because no accepted ADR resolves L2 snapshot/increment/gap semantics and
acquisition/publication/live wiring were outside #316. The L2 branch decision
must ratify that schema by ADR or supersede it.

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
`P05b` and `P06`. Implementation is `MISSING`; `SCOPE.md` Part B authorizes
the inventory and records start gates, and no implementation issue is open
yet. Fixed owner inputs: identity-preserving canonical profiles (P01), hosts
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

The current scope is DG-K Process Topology v1. Its design gate is closed
(ADR-0068, ADR-0069, #319) and `SCOPE.md` Part B authorizes `P01`-`P06`: J02
wiring of `J10`/`J12`/`J13` (P05a/P05b), J03 dispatch and a single worker
(P04a/P04b), the runtime store on PostgreSQL (P02), one canonical identity
module (P01), host boundaries (P03) and the deck handoff over J02 (P06).
Startable once their issues exist: `P01`, `P02`, `P03`, `P05a`. No
implementation issue is open yet.

No new runtime/product frontier is authorized by this document. Neither the
Wave 7 closeout, the Omega stabilization line, the `DG-J` resolution nor the
Wave 8 closeout activates H06, I06-I07, J07/J08, RL, live broker execution, second venue or
L1/L2/L3 market depth. J07 remains `MISSING`.

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
DG-K   Process Topology v1                               ADRs ACCEPTED (#319); P01-P06 authorized, MISSING; before Wave 9
Wave 9  Reinforcement Learning                            I06 strategic RL, I07 execution RL
Wave 10 Paper / Live product                              J07 then J08 after required data/execution/ops gates
```

See `ROADMAP.md`'s "Post-Wave-7 sequencing" for the explicit priority rationale
(usability of existing capabilities first); this is a priority order, not
concurrent authorization for Wave 9 or Wave 10.

Parallelism is allowed whenever DAG dependencies are satisfied. The closed Live Ingest scope nevertheless preserves the repository rule that governed it: one bounded mutation slice at a time.
