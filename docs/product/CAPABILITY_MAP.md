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
| Data | Historical Bybit acquisition | FROZEN | PARTIAL | Source/Producer | A07 | Narrow reference vertical exists; not a general ingest runtime. |
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
| Representation | Candle materialization identity | OPEN_BLOCKING | MISSING | Representation | D05 | DG-A candle-materialization branch; not a prerequisite of canonical H01. |
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
| Interfaces | Canonical API transport | RESOLVED | COMPLETE | API Runtime | J02 | ADR-0050; HTTP JSON transport over the frozen Consumer API semantics, implemented in Wave 7. |
| Runtime | Job runtime | OPEN_BLOCKING | MISSING | Runtime | J03 | DG-G job branch; durable identity/retry/result semantics. |
| Clients | CLI | RESOLVED | COMPLETE | Client Layer | J04 | Thin canonical CLI client integrated in Wave 7. |
| Clients | TUI | RESOLVED | COMPLETE | Client Layer | J05 | Thin canonical TUI client integrated in Wave 7. |
| Clients | App UI | RESOLVED | COMPLETE | Client Layer | J06 | Thin canonical App UI client integrated in Wave 7. |
| Runtime | Paper/shadow mode | RESOLVED | MISSING | Runtime | J07 | Vertical gate before live operation. |
| Runtime | Live product mode | RESOLVED | MISSING | Runtime/Operations | J08 | Requires explicit operational authorization; roadmap state is not authorization. |
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
| Wave 1 (`implement/wave-1`, issues #51-#77) | CONCLUDED | 2026-09-19; issue #73's Human Golden E2E closeout (1440-candle 1m D03, official `GOLDEN E2E: PASS`) is closed and credited. |
| Wave 2 (`implement/wave-2`, issues #83,#85,#86) | COMPLETE | E06/F02/F03 merged (PR #88/#89/#90); reconciled by issue #87. |
| Wave 3 (`implement/wave-3`) | COMPLETE | F01-F08 implementation complete; F03 authority reconciled by ADR-0038/PR #103; F07/F08 integrated by PR #99/#104. |

## Current frontier

Wave 7 API & Platform Transport is closed on `implement/wave-7`: ADR-0050 and PR #225/#226/#227/#228 complete J02/J04/J05/J06. Issue #222 was closed by explicit operator exception during expedited closeout, so this state does not claim an additional dedicated Golden E2E artifact.

The DG-B long-gap remediation proposition remains disposed `NO_AUTHORITATIVE_REPAIR_PATH_PROVEN` (#110): A11 may record an explicit non-complete interval and continue, but the project may not claim such an interval filled/lossless until an authoritative repair source/path proves the missing support. ADR-0044 carries this forward as accepted fail-closed behavior rather than reopening DG-B.

DG-B has no remaining B06 atom blocker after Wave 6; its only still-disposed proposition is issue #110's long-gap remediation rule. DG-H has no currently identified open atom after K07/K09 completion. DG-G remains open only for I06, I07 and J03. DG-I is resolved by Wave 7. J07 remains missing and is not implemented by the Wave 7 closeout.

No new runtime/product frontier is selected by this closeout. J07, J08, RL, broker/live execution, second venue and L1/L2/L3 market depth remain outside the completed Wave 7 scope. Frontier/readiness state is **not concurrent implementation authorization**.

See [`ROADMAP.md`](ROADMAP.md) for macro progression and [`CAPABILITY_DAG.md`](CAPABILITY_DAG.md) for exact dependency/decision-gate semantics.
