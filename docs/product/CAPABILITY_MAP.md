# Capability Map

This is the compact canonical-state snapshot. Atom-level dependencies, acceptance propositions, decision gates and execution readiness are authoritative in [`CAPABILITY_DAG.md`](CAPABILITY_DAG.md). Legacy evidence is reference evidence only.

Decision state and implementation state are intentionally separate.

Implementation-state vocabulary is exactly `COMPLETE | PARTIAL | MISSING`.

| Domain | Capability | Decision State | Implementation State | Target owner | Atom(s) | Notes |
|---|---|---|---|---|---|---|
| Data | `trade-v1` canonical record | FROZEN | COMPLETE | Data Plane | A01 | Frozen schema/identity/ordering semantics. |
| Data | Dataset/partition identity | FROZEN | COMPLETE | Data Plane | A02 | `dataset-manifest-v1` retained; v2 additive for source-acquired topology. |
| Data | Source-acquired lineage v2 | FROZEN | COMPLETE | Data Plane | A03 | No synthetic parent; source provenance remains evidence-owned. |
| Data | Declared coverage | FROZEN | COMPLETE | Data Plane | A04 | Coverage is explicit and distinct from observed row bounds. |
| Data | Catalog/schema bootstrap | RESOLVED | COMPLETE | Data Plane/Engineering | A05 | Repository schema bytes authoritative; conflicts fail closed. |
| Data | Canonical Parquet materialization | FROZEN | COMPLETE | Producer | A06 | First historical vertical proven. |
| Data | Historical Bybit acquisition | FROZEN | PARTIAL | Source/Producer | A07 | One-UTC-day BTCUSDT-linear v1 profile; multi-day/date-range consumers loop day-by-day. ADR-0054; not a general ingest runtime. |
| Data | S13 certification | FROZEN | COMPLETE | Producer | A08 | Durable authoritative quality evidence. |
| Data | S14 publication/eligibility | FROZEN | COMPLETE | Producer | A09 | First vertical catalog publication proven. |
| Data | Backfill/repair | FROZEN | COMPLETE | Data Plane | A10 | ADR-0033; deterministic repair-intent/candidate/atomic-cutover foundation. |
| Data | Live trades acquisition | FROZEN | COMPLETE | Data Plane | A11 | ADR-0040; Bybit BTCUSDT live mapping/session/dedup/cutover/reconnect plus canonical publication path integrated by PR #112 and proven on the real provider/server by PR #113. ADR-0044 governs unresolved long gaps as explicit non-complete coverage unless future authoritative repair evidence exists. |
| Data | Second-venue trades | OPEN_DEFERABLE | MISSING | Data Plane | A12 | Triggered by real second-provider evidence; do not design a generic resolver early. |
| Data | L1 contract/acquisition | OPEN_BLOCKING | MISSING | Data Plane | A13 | DG-C L1 branch, triggered by a concrete feed; does not activate L2. |
| Data | L2 contract/acquisition | OPEN_BLOCKING | MISSING | Data Plane | A14 | DG-C L2 branch, only when L2 is selected. |
| Data | L3/MBO contract | OPEN_DEFERABLE | MISSING | Data Plane | A15 | Explicitly wait for a real L3 feed. |
| Data | General quality lifecycle | RESOLVED | COMPLETE | Data Plane | A16 | First vertical complete, reused by A10; general lifecycle beyond it belongs to the relevant DG-B branch. |
| Access | Data Access owner/boundary | RESOLVED | COMPLETE | Data Access | B01 | Canonical `quant_platform.access`; dependency direction mechanically enforced. |
| Access | Bounded historical scan | FROZEN | COMPLETE | Data Access | B02,B07 | `DataGateway.scan()` is the canonical bounded historical seam. |
| Access | Result identity/provenance | FROZEN | COMPLETE | Data Access | B03 | Semantic/source identity distinct from physical locator. |
| Access | Non-contiguous coverage read | FROZEN | COMPLETE | Data Access | B04 | Explicit `ALLOW_PARTIAL` coverage reads; ADR-0029. |
| Access | Durable DatasetSnapshot | OPEN_DEFERABLE | MISSING | Data Access | B05 | Shape waits for a concrete durable-replay requirement. |
| Access | Live access/cursor | FROZEN | COMPLETE | Data Access | B06 | ADR-0047 freezes deterministic consumer cursor, replay, disconnect and explicit gap semantics; PR #204 implements `DataGateway.live_stream()` and PR #212 proves the bounded Wave 6 live-consumer path. |
| Access | Schema evolution beyond accepted versions | OPEN_DEFERABLE | MISSING | Data Access | B08 | Resolve against real next-version evidence. |
| Application | ASS-01 ownership/enforcement | RESOLVED | COMPLETE | Application | C01 | `quant_platform.application` is the canonical in-process composition owner; PR #27 merged. |
| Application | ASS-02 semantic selector resolution | FROZEN | COMPLETE | Application | C02 | PR #30 integrated the reviewed `trades@1` semantic selector resolution. |
| Application | ASS-02 result/error translation | FROZEN | COMPLETE | Application | C03 | PR #41 integrated reviewed candidate `f382a2e6...`; exact-head integrity #91 PASS. |
| Application | ASS-03 tool convergence | RESOLVED | COMPLETE | Application | C04 | Issue #40/PR #44, closed 2026-09-14; `TOOLS_PENDING_ASS03`/`TOOLS_TESTS_PENDING_ASS03` empty, boundary test passing. |
| Application | Configuration convergence | RESOLVED | COMPLETE | Application/Engineering | C05 | Frozen convention: CLI > env > declared default > fail; typed immutable capability-specific config; Application owns composition. |
| Application | Multi-capability resolver | OPEN_DEFERABLE | MISSING | Application | C06 | Triggered by a real second venue/representation. |
| Representation | Representation identity | RESOLVED | PARTIAL | Representation | D01 | Distinct from DatasetIdentity. |
| Representation | CandleDefinition v1 | FROZEN | COMPLETE | Representation | D02 | Accepted semantic contract; runtime is a separate capability. |
| Representation | Historical Candle computation | FROZEN | COMPLETE | Representation | D03 | On-demand CLOSED candles. |
| Representation | Incremental/live Candle computation | FROZEN | COMPLETE | Representation | D04 | PR #205 implements incremental/live candle computation; PR #208 composes B06 into D04 with gap-safe watermarking; PR #212 proves the bounded Wave 6 live-candle path. |
| Representation | Candle materialization identity | FROZEN | MISSING | Representation | D05 | ADR-0059 freezes representation-artifact identity and the replay-input-profile shape; no runtime/materialization/replay access is implemented. Not a prerequisite of canonical H01. |
| Representation | Footprint representation | FROZEN | COMPLETE | Representation | D06 | FootprintDefinition v1 exact duration/tick-grid/sparse levels/finality; ADR-0027. |
| Features | Definition/set/artifact separation | FROZEN | COMPLETE | Feature Engine | E01 | Architectural identity separation accepted. |
| Features | FeatureDefinition v1 | FROZEN | COMPLETE | Feature Engine | E02 | ADR-0026 accepted; runtime semantic foundation implemented. |
| Features | FeatureSet catalog/provider | RESOLVED | PARTIAL | Feature Engine | E03 | Existing catalog foundation retained. |
| Features | FeatureArtifact/materialization | FROZEN | COMPLETE | Feature Engine | E04 | ADR-0034; deterministic identity, SupportShape, FINAL-only sealing, attributable-evidence caller discipline. |
| Features | H01 pure imbalance kernel | RESOLVED | COMPLETE | Feature Engine | E05 | Narrow Legacy Harvest ADOPT boundary. |
| Features | H01 canonical integration | FROZEN | COMPLETE | Feature Engine | E06 | ADR-0035; Diagonal + Stacked are independent FINAL-Footprint features in one `h01_imbalance@1` FeatureSet; implemented, issue #83/PR #88. |
| Features | Generic provider extension | OPEN_DEFERABLE | MISSING | Feature Engine | E07 | Wait for a real second provider. |
| Research | HypothesisSpec | RESOLVED | COMPLETE | Research | F01 | Depends on FeatureDefinition for the canonical vertical. |
| Research | EventSpec/detection | RESOLVED | COMPLETE | Research | F02 | Requires FeatureArtifact for the full vertical; implemented, issue #85/PR #89. |
| Research | OutcomeSpec/Outcome | FROZEN | COMPLETE | Research | F03 | ADR-0038 ratifies canonical Outcome v1 semantics and EOD evidence; implementation issue #86/PR #90, adversarial closeout issue #102/PR #103. |
| Research | Event studies/sweeps | RESOLVED | COMPLETE | Research | F04 | Reproducible study population/aggregates; implemented, issue #92/PR #94. |
| Validation | Walk-forward schedule | RESOLVED | COMPLETE | Validation | F05 | Deterministic pure temporal atom. |
| Validation | Availability/purge/embargo | FROZEN | COMPLETE | Validation | F06 | ADR-0031. |
| Validation | Labels/censoring/lockbox | FROZEN | COMPLETE | Validation | F07 | ADR-0036; outcome-derived labels, exact target support and terminal lockbox implemented, issue #96/PR #99. |
| Validation | DSR/PBO | FROZEN | COMPLETE | Research/Validation | F08 | ADR-0037; Validation-owned DSR-L/full-CSCV runtime implemented, issue #98/PR #104. |
| Strategy | StrategySpec/DecisionIntent | RESOLVED | COMPLETE | Strategy | G01 | Strategy remains upstream of execution; integrated in Wave 4. |
| Strategy | Policy composition | RESOLVED | COMPLETE | Strategy | G02 | Deterministic composition integrated in Wave 4. |
| Strategy | Risk/sizing | RESOLVED | COMPLETE | Strategy | G03 | Reproducible capital allocation and sizing integrated in Wave 4. |
| Strategy | Session/cooldown semantics | FROZEN | COMPLETE | Strategy | G04 | ADR-0045 freezes session/calendar/cooldown semantics. |
| Execution | Order/Fill lifecycle | RESOLVED | COMPLETE | Execution | H01 | Explicit state transitions integrated in Wave 4. |
| Execution | Cost/synthetic-fill model | RESOLVED | COMPLETE | Execution | H02 | Fee/slippage/fill model integrated in Wave 4. |
| Execution | Conflict/partial-fill semantics | FROZEN | COMPLETE | Execution | H03 | ADR-0046 freezes execution conflict and intra-bar fill semantics. |
| Portfolio | Portfolio/ledger | RESOLVED | COMPLETE | Portfolio | H04 | Deterministic accounting integrated in Wave 4. |
| Execution | Deterministic replay | RESOLVED | COMPLETE | Execution | H05 | Uses `DataGateway.scan()`; PR #160 integrated H05 and PR #161/#146 proved V7. |
| Portfolio | Multi-asset execution | OPEN_DEFERABLE | MISSING | Portfolio | H06 | Wait for concrete product scope. |
| Experiments | Study/Trial/Run/Artifact semantic model | RESOLVED | COMPLETE | Experiment System | I01 | One canonical experiment identity family. |
| Experiments | Canonical experiment persistence | RESOLVED | COMPLETE | Experiment System | I02 | One restart-safe canonical persistence model. |
| Experiments | Trial accounting/comparison | RESOLVED | COMPLETE | Experiment System | I03 | PR #178 / issue #171; comparable trial population identity, resume/idempotency and metric comparison. |
| ML | Supervised input/selection | RESOLVED | COMPLETE | Learning | I04 | PR #177 / issue #172; feature/label selection reuses Validation availability and fails closed on leakage. |
| ML | Supervised training/evaluation | RESOLVED | COMPLETE | Learning | I05 | PR #179 / issue #173; deterministic baseline training/evaluation with model, prediction and metric artifact identities. |
| RL | Strategic RL contract | OPEN_BLOCKING | MISSING | Strategic RL | I06 | DG-G strategic-RL branch. |
| RL | Execution RL contract | OPEN_BLOCKING | MISSING | Execution RL | I07 | DG-G execution-RL branch; structurally separate from strategic RL. |
| Interfaces | Consumer API semantic boundary | FROZEN | COMPLETE | Application/API | J01 | ADR-0020; semantic API is not a DataGateway wrapper and does not imply transport runtime. |
| Interfaces | Canonical API transport | RESOLVED | COMPLETE | API Runtime | J02 | ADR-0050; WebSocket transport over the frozen Consumer API semantics, implemented in Wave 7. Amendment 1 froze a 50,000-row/16 MiB result bound; auth/TLS for non-loopback deployment remains explicitly open (DG-J G1). |
| Runtime | Job runtime | FROZEN | COMPLETE | Runtime | J03 | ADR-0062; durable admission/lifecycle/attempt/recovery record implemented by PR #307 (Wave 8). No dispatcher, worker or PostgreSQL store (DG-K). |
| Clients | CLI | RESOLVED | COMPLETE | Client Layer | J04 | Thin canonical CLI client integrated in Wave 7. |
| Clients | TUI | RESOLVED | COMPLETE | Client Layer | J05 | Thin canonical TUI client integrated in Wave 7. |
| Clients | App UI | RESOLVED | COMPLETE | Client Layer | J06 | Thin canonical App UI client integrated in Wave 7. |
| Runtime | Paper/shadow mode | RESOLVED | MISSING | Runtime | J07 | Vertical gate before live operation. |
| Runtime | Live product mode | RESOLVED | MISSING | Runtime/Operations | J08 | Requires explicit operational authorization; roadmap state is not authorization. |
| Interfaces | Authenticated/TLS transport v1 | FROZEN | COMPLETE | API Runtime | J09 | ADR-0063; WSS+mTLS 1.3, exact-fingerprint principal mapping, `j02.market_data.read` scope implemented by PR #310 (Wave 8). |
| Interfaces | Strategy Consumer-API seam v1 | FROZEN | COMPLETE | Application/API | J10 | ADR-0065 §2; in-process `strategy-compose-v1` seam, PR #308 (Wave 8). Not reachable over J02 (DG-K P05). |
| Interfaces | Replay Consumer-API seam v1 | OPEN_DEFERABLE | MISSING | Application/API | J11 | ADR-0065 §5 explicitly defers: no accepted identity for `HistoricalReplayRuntime`'s `feature_provider` callable yet. Not planned (owner decision 2026-10-10, DG-K #318): replay runs on the deck over K12/K13. |
| Interfaces | Validation Consumer-API seam v1 | FROZEN | COMPLETE | Application/API | J12 | ADR-0065 §3 + Amendment 1; in-process Validation seam, PR #312 (Wave 8). Not reachable over J02 (DG-K P05). |
| Interfaces | Training Consumer-API seam v1 | FROZEN | COMPLETE | Application/API | J13 | ADR-0065 §4 + Amendment 2; in-process bounded Training seam, PR #315 (Wave 8). Not reachable over J02 (DG-K P05). |
| Interfaces | Streaming/live transport extension v1 | FROZEN | COMPLETE | API Runtime | J14 | ADR-0066; `j14-framed-result-v1` framing/verification/resume module, PR #320 (Wave 8). Not yet dispatched by the J02 server; no live family. |
| Clients | Omega API client adapter v1 | FROZEN | COMPLETE | Client Layer | J15 | ADR-0067; Omega remote client in `clients/omega`, `j02.market_data.read` only, PR #321 (Wave 8). |
| Operations | Provisioning/fixtures/CI | RESOLVED | COMPLETE | Engineering | K01 | Repository validation foundation complete. |
| Operations | Server/runtime identity | FROZEN | COMPLETE | Operations | K02 | ADR-0041; real-server proof in PR #113 verified non-root least privilege, minimum catalog rights, physical storage topology, and backup-authority separation. |
| Operations | Observability | RESOLVED | COMPLETE | Operations | K03 | Minimum externally observable health/provenance/failure transitions. |
| Operations | Capacity observation | RESOLVED | COMPLETE | Operations | K04 | Observational only. |
| Operations | Health/pressure policy | FROZEN | COMPLETE | Operations | K05 | ADR-0028. |
| Operations | RAW/source protection | FROZEN | COMPLETE | Operations/Data Plane | K06 | ADR-0032; attributable-evidence protection-identity/assessment seam, no backup dependency. |
| Operations | Tier relocation | FROZEN | COMPLETE | Operations/Data Plane | K07 | ADR-0048 freezes crash-safe old-or-new-valid relocation semantics; PR #209 implements storage tier relocation and PR #212 proves the bounded Wave 6 storage lifecycle path. |
| Operations | Backup/restore proof | FROZEN | COMPLETE | Operations | K08 | ADR-0039; PR #111 implements identity-bound backup/isolated restore and PR #113 supplies the previously pending real deployment-independence evidence. |
| Operations | Retention/deletion authority | FROZEN | COMPLETE | Operations | K09 | ADR-0049 freezes retention/deletion authority; PR #211 implements governed deletion and PR #212 proves the bounded Wave 6 storage lifecycle path. |
| Operations | Checkpoint/recovery | FROZEN | COMPLETE | Operations/Data Plane | K10 | ADR-0042; PR #114 implements persisted checkpoint/recovery and the full hermetic proof matrix; PR #122 supplies the bounded real-server restart/deployed checkpoint-path proof (`K10_REAL_RESTART_PROOF: PASS`). |
| Governance | Governance-state consistency | RESOLVED | COMPLETE | Governance | K11 | Canonical authority reconciled through Wave 4 closeout by governance issue #147. |
| Operations | Server-owned admitted-input export/reference v1 | FROZEN | COMPLETE | Operations | K12 | ADR-0064; sealed `AdmittedInputManifestV1`/`admission_id` and delivery states, PR #309 (Wave 8). No physical byte transfer or deck reader. |
| Operations | Governed experiment-result import/registration v1 | FROZEN | COMPLETE | Operations | K13 | ADR-0064 §3; full-bundle refusal and idempotent import, PR #311 (Wave 8). Not one transaction across PostgreSQL and SQLite (DG-K P02). |
| Topology | Canonical identity serializer v1 | FROZEN | MISSING | Engineering/Application | P01 | ADR-0068 (#319); named versioned byte profiles, no identity changes. |
| Topology | Runtime store on PostgreSQL v1 | FROZEN | MISSING | Operations/Application | P02 | ADR-0068 (#319); `runtime` schema for J03/K12/K13; atomic K13 registration. |
| Topology | Executable host boundaries v1 | FROZEN | MISSING | Engineering | P03 | ADR-0068 (#319); hosts stay in `tools/`, per-host import rules. |
| Topology | J03 handler registry and dispatch v1 | FROZEN | MISSING | Application | P04a | ADR-0068 (#319); closed data-local handler set. |
| Topology | Single J03 worker process v1 | FROZEN | MISSING | Runtime | P04b | ADR-0068 (#319); exactly one worker, no broker. |
| Topology | J02 carriage of synchronous J10/J12/J13 v1 | FROZEN | MISSING | API Runtime | P05a | ADR-0069 (#319); wire messages and one scope per operation family. |
| Topology | J02 submit/status/result for J03 data-local jobs v1 | FROZEN | MISSING | API Runtime | P05b | ADR-0069 (#319); requires P04b. |
| Topology | Deck handoff over J02 v1 | FROZEN | MISSING | API Runtime/Operations | P06 | ADR-0069 (#319); K12 byte delivery and K13 submission, deck-side client, feature-provider code identity in the bundle. |

## Gate / evidence state

| Capability | State | Notes |
|---|---|---|
| Contract Freeze Gate | PASSED | ADR-0023 accepted. |
| Conformity Implementation Gate | PASSED | All ADR-0023 exit criteria satisfied. |
| Human Golden Bybit BTCUSDT E2E | PASS | Exact accepted reference vertical. |
| Package Boundary / Modular Monolith Foundation v1 | COMPLETE | Ownership/dependency enforcement established. |
| Legacy Capability Harvest Audit v1 | COMPLETE | 31 capabilities classified; H14 REVIEW resolved to ADAPT by ADR-0037. |
| ASS-01 Application ownership/enforcement | COMPLETE | PR #27 integrated. |
| ASS-02 semantic selector resolution (C02) | COMPLETE | PR #30 integrated; reviewed exact-head CI passed. |
| ASS-02 result/error translation (C03) | COMPLETE | PR #41 integrated reviewed head `f382a2e6...`; integrity #91 passed on exact head. |
| ASS-02 in-process Application vertical | COMPLETE | C02 + C03 complete; does not imply C05/C04/API/jobs/clients. |
| K11 Governance-state consistency | COMPLETE | Canonical authority reconciled through Wave 4 closeout by governance issue #147. |
| E02 FeatureDefinition v1 semantic foundation | COMPLETE | ADR-0026 accepted; immutable runtime model and targeted tests implemented. |
| D06 FootprintDefinition v1 representation foundation | COMPLETE | ADR-0027 accepted; immutable historical FINAL Footprint v1 runtime implemented. |
| B04 Non-contiguous coverage reads v1 | COMPLETE | ADR-0029 accepted; explicit `ALLOW_PARTIAL` DataGateway policy implemented. |
| K06 RAW/source protection v1 | COMPLETE | ADR-0032 accepted; attributable-evidence `SafetyRelevanceAssertion` pattern implemented. |
| A10 Backfill/repair v1 | COMPLETE | ADR-0033 accepted; two known limitations tracked (coverage-trigger re-verification, partial candidate identity binding). |
| E04 FeatureArtifact v1 | COMPLETE | ADR-0034 accepted; `SupportShape`, FINAL-only sealing, `InitVar` construction guard, exact-Fraction equivalence. |
| E06 H01 canonical integration v1 | COMPLETE | ADR-0035 accepted; bounded implementation completed, issue #83/PR #88. |
| F02 EventSpec/detection v1 | COMPLETE | Traceable event rule + availability evidence implemented, issue #85/PR #89. |
| F03 OutcomeSpec/Outcome v1 | COMPLETE | ADR-0038 canonical semantic authority; issue #86/PR #90 implementation plus issue #102/PR #103 adversarial closeout. |
| F04 Event studies/sweeps v1 | COMPLETE | Reproducible study population/aggregates and parameter sweep runtime implemented, issue #92/PR #94. |
| F07 labels/censoring/lockbox v1 | COMPLETE | ADR-0036; implementation integrated via issue #96/PR #99. |
| F08 DSR/PBO robust comparison v1 | COMPLETE | ADR-0037; implementation integrated via issue #98/PR #104. |
| K08 backup/restore semantics v1 | COMPLETE | ADR-0039; PR #111 implementation/isolated restore plus PR #113 real deployment-independence evidence. |
| A11 Bybit live acquisition semantics v1 | COMPLETE | ADR-0040; PR #112 implementation and real-provider proof, with PR #113 proving canonical publication/catalog/DataGateway composition on the target server. |
| K02 live-ingest runtime identity v1 | COMPLETE | ADR-0041; PR #113 real-server least-privilege, storage and database authority proof. |
| K10 live-ingest checkpoint/recovery v1 | COMPLETE | ADR-0042; PR #114 hermetic implementation/proof matrix integrated; PR #122 closes the real-server restart/deployed checkpoint-path proof (issues #109, #121). |
| Live Ingest Server Production Readiness v1 | COMPLETE | Closed for the first Bybit BTCUSDT producer operating path by ADR-0043/ADR-0044, the supervised systemd path, target-host proof and DataGateway readback PASS; does not complete B06, K07, K09, J08 or live product mode. |
| Wave 4 Strategy / Replay | COMPLETE | `G01`-`G04` and `H01`-`H05` integrated; G04/H03 frozen by ADR-0045/ADR-0046; PR #161 / issue #146 records Golden V7 deterministic replay PASS. |
| Wave 5 Experiment / Supervised ML | COMPLETE | I03/I04/I05 integrated by PR #178/#177/#179; PR #180 / issue #174 records Golden supervised E2E PASS with stable projection, run, metric and artifact identities. |
| Wave 6 Live Consumer Data Plane & Storage Lifecycle | COMPLETE | B06/D04/K07/K09 are frozen and implemented by ADR-0047/0048/0049, PR #204/#205/#208/#209/#211, and Golden proof PR #212. |
| Wave 7 API & Platform Transport | COMPLETE | J02/J04/J05/J06 are implemented by ADR-0050 and PR #225/#226/#227/#228. Issue #222 is closed by explicit operator exception without claiming an additional Golden proof artifact. |
| Omega external-audit stabilization line (`implement/omega`, tracking #232) | COMPLETE for the audited findings | Not a wave; a post-Wave-7 stabilization line reconciling an external audit from integrating `NeoNix-Lab/omega`. 25 PRs (#254, #256-#278) merged: safety tooling/package hygiene (#254), workflow non-wave integration-branch detection (#256), exception-handling narrowing (#257), SQLite covering-index preflight (#258), systemd hardening (#259), J02 result-size bound (ADR-0050 Am.1, #260), StrategySpec extensions (ADR-0051/52/53, #261-263), A07 day-boundary (ADR-0054, #264), H05 repeated-entry gating (#265-266), `k_eff` permanent disposition (ADR-0037 Am.1, #267), `shell=True` removal (#268), promotion-candidate validation (#269), public dependency surface (#270), Omega validation bridge (ADR-0055, #271), Golden proof/test-double boundary (ADR-0056, #272), server/deck topology (ADR-0057, #273), replay sweep orchestration boundary (ADR-0058, #274), D05 representation-replay input (ADR-0059, #275), canonical replay I/O profile (ADR-0060, #276), replay summary mode (ADR-0061, #277), and replay strategy-identity caching (#278). This governance pass (issue "Omega stabilization-line reconciliation and platform-v1.0 service-topology scope") reconciles the above into governance state; it adds no new code itself. |
| `DG-J` Remote Service Topology design-gate line (issues #282-#287, parent #281) | RESOLVED for all six gates | Six design-gate issues on `implement/omega` each produced one ACCEPTED ADR: G1/`J09` (ADR-0063, #283/PR #290), G2/`J03` (ADR-0062, #282/PR #289), G3/`K12`+`K13` (ADR-0064, #284/PR #291), G4/`J10`,`J12`,`J13` (ADR-0065, #285/PR #292; `J11` explicitly deferred), G5/`J14` (ADR-0066, #286/PR #293), G6/`J15` (ADR-0067, #287/PR #294). Issue #288 reconciles these outcomes into governance state and authorizes the Wave 8 implementation inventory; `implement/omega` is closed as the bounded design-integration line for this scope. |
| Wave 8 Full Platform API & Remote Service Topology | COMPLETE (operator closeout #327) | J03/J09/J10/J12/J13/J14/J15/K12/K13 implemented by PR #307-#312, #315, #320, #321 and promoted by PR #322; owner-accepted extras #313/#314 by PR #323/#324 via `implement/wave-8.1` and PR #326. J10/J12/J13 are in-process seams only; no Wave 8 Golden proof artifact is claimed. PR #317 (A14 L2 groundwork) does not complete A14. |
| `DG-K` Process Topology v1 design gate (governance #318, design gate #319) | RESOLVED | ADR-0068 (process topology v1) and ADR-0069 (J02 carriage of consumer seams and deck handoff v1) accepted; P01-P06 `FROZEN`/`MISSING` and authorized in `SCOPE.md` Part B; no implementation issue opened yet. |
| Wave 1 (`implement/wave-1`, issues #51-#77) | CONCLUDED | 2026-09-19; issue #73's Human Golden E2E closeout (1440-candle 1m D03, official `GOLDEN E2E: PASS`) is closed and credited. |
| Wave 2 (`implement/wave-2`, issues #83,#85,#86) | COMPLETE | E06/F02/F03 merged (PR #88/#89/#90); reconciled by issue #87. |
| Wave 3 (`implement/wave-3`) | COMPLETE | F01-F08 implementation complete; F03 authority reconciled by ADR-0038/PR #103; F07/F08 integrated by PR #99/#104. |

## Current frontier

Wave 7 API & Platform Transport is closed on `implement/wave-7`: ADR-0050 and PR #225/#226/#227/#228 complete J02/J04/J05/J06. Issue #222 was closed by explicit operator exception during expedited closeout, so this state does not claim an additional dedicated Golden E2E artifact.

The post-Wave-7 `implement/omega` stabilization line reconciles an external
audit (tracking #232) rather than opening a new wave. It is now reconciled
into governance state (see the checkpoint row above); it resolved eight design
gates (ADR-0054-0061) and nine implementation/hardening slices, and amended
ADR-0037 and ADR-0050. It does not select a new runtime/product frontier by
itself.

The DG-B long-gap remediation proposition remains disposed `NO_AUTHORITATIVE_REPAIR_PATH_PROVEN` (#110): A11 may record an explicit non-complete interval and continue, but the project may not claim such an interval filled/lossless until an authoritative repair source/path proves the missing support. ADR-0044 carries this forward as accepted fail-closed behavior rather than reopening DG-B.

DG-B has no remaining B06 atom blocker after Wave 6; its only still-disposed proposition is issue #110's long-gap remediation rule. DG-H has no currently identified open atom after K07/K09 completion. DG-G remains open only for I06 and I07 (J03 is frozen under ADR-0062 and its durable record is implemented by Wave 8). DG-I is resolved by Wave 7; J02's auth/TLS is frozen under ADR-0063 and implemented by `J09` (Wave 8). J07 remains missing and is not implemented by any closeout so far.

Wave 8 — Full Platform API & Remote Service Topology v1 is closed by operator
closeout (issue #327): `J03`, `J09`, `J10`, `J12`, `J13`, `J14`, `J15`, `K12`,
`K13` are implemented and on `main` (PR #322, #326) within the bounds in their
rows; `J11` remains deferred; no Wave 8 Golden proof artifact is claimed. The
current scope is DG-K Process Topology v1: its design gate is resolved by
ADR-0068 and ADR-0069 (#319), and `SCOPE.md` Part B authorizes P01-P06 (J02
wiring of `J10`/`J12`/`J13`, J03 dispatch and one worker, the runtime store on
PostgreSQL, one canonical identity module, host boundaries and the K12/K13
deck handoff over J02). No implementation issue is open yet. `J11` is not
planned. J07, J08, RL, broker/live execution, second venue
and L1/L2/L3 market depth remain outside every closed scope (`A14` has
groundwork from PR #317 only). Frontier/readiness state is **not concurrent
implementation authorization**.

See [`ROADMAP.md`](ROADMAP.md) for macro progression and [`CAPABILITY_DAG.md`](CAPABILITY_DAG.md) for exact dependency/decision-gate semantics.
