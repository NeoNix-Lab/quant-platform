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
| Data | L2 contract/acquisition | FROZEN | COMPLETE | Data Plane | A14 | ADR-0070 (#367), Amendments 1 (#402 / PR #403) and 2 (#416 / PR #417). Batch v1 CLOSED on `implement/l2` by governance #442: A14a-A14e COMPLETE (PR #375, #379, #401, #411 + #417, #408) and golden accepted (PR #413 + #430, `1cdd78f`, 11/11 claims). Historical Bybit BTCUSDT `ob200` ingest through consumer read, with explicit gaps; no live L2 or `ob500`. Independent of `A13`; `A14f` and `A14g` remain held outside this closure. Promotion to `main` is a separate wave closeout PR |
| Data | L2 ordering/certification profile v1 | FROZEN | COMPLETE | Data Plane | A14a | ADR-0070 §1, §2, §5, §7; #374 / PR #375. Numeric `seq` ordering, `exchange_ts = cts`, `provider_ts = ts`, `u + 1` continuity and fail-closed certification profile. E2-E4 measured on one full `ob200` day (#430): no restart, mid-day re-snapshot, absent-level delete or crossed state; restart survival and population rates remain unproven; thresholds unchanged |
| Data | L2 Parquet materializer/scanner v1 | FROZEN | COMPLETE | Data Plane | A14b | ADR-0070 §3, §4 and Amendment 1; #376 / PR #379. Source-event Parquet materializer/scanner; `uint64` order columns round-trip exactly to the string-typed schema; `dt=YYYY-MM-DD/seg=NNN`, byte-identical reproduction. No reconstructed book or trade-path change; `raw` support remains an A14g requirement |
| Data | L2 publication/catalog bridge v1 | FROZEN | COMPLETE | Producer | A14c | ADR-0070 §4, §5 and Amendment 1; #399 / PR #401. L2 certification, publication and catalog bridge, `kind="l2"`; one clean anchored run per eligible partition, published `valid`, continuity evidence bound to bytes; PostgreSQL proof requires no catalog DDL change |
| Data | Bybit historical L2 ingest v1 | FROZEN | COMPLETE | Source/Producer | A14d | ADR-0070 §2, §4, §7 and Amendments 1-2; #410 / PR #411, conformed by #416 / PR #417. Historical `ob200` ingest cuts discontinuities into `dt=.../seg=NNN` runs. UTC day owns events by `exchange_ts`, strictly congruent with `trade-v1`; contiguous source-head/tail blocks are retained and evidenced, never published into the adjacent day automatically. Explicit head/tail `known_gap`; no blanket refusal for a valid post-midnight tail, but interior out-of-day placement/regression still refuses. Byte-identical re-acquisition; no `ob500` |
| Data | L2 consumer read v1 | FROZEN | COMPLETE | Data Access | A14e | ADR-0070 §6; #407 / PR #408. `l2_book_events@1` under `j02.market_data.read`; numeric sequence order, coverage/gaps/anchored flag, per-partition source-acquired/projected origin; gaps never filled, no projected-row `u + 1` assumption, byte-based `result_too_large` bound. Frozen J02 v1 and trade path unchanged |
| Data | Bybit live L2 ingest/resync v2 | FROZEN | MISSING | Data Plane | A14f | ADR-0070; start gate released by #448 (maintainer decision, soak plan = #420); MISSING: no A14f evidence merged (PR #447 open, uncredited); design points D1-D4 open in OPEN_DECISIONS; outside A14 v1 closure. |
| Data | Bybit ob500 raw ingest/top-200 projection | FROZEN | MISSING | Source/Producer | A14g | ADR-0070; held under the unchanged SCOPE.md Part B start gate; outside A14 v1 closure; not authorized by #442. |
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
| Runtime | Job runtime | FROZEN | COMPLETE | Runtime | J03 | ADR-0062; durable admission/lifecycle/attempt/recovery record implemented by PR #307 (Wave 8). No dispatcher, worker or PostgreSQL store (DG-K). Since DG-K (#381): PostgreSQL store, dispatch registry (empty) and single worker host exist. |
| Clients | CLI | RESOLVED | COMPLETE | Client Layer | J04 | Thin canonical CLI client integrated in Wave 7. |
| Clients | TUI | RESOLVED | COMPLETE | Client Layer | J05 | Thin canonical TUI client integrated in Wave 7. |
| Clients | App UI | RESOLVED | COMPLETE | Client Layer | J06 | Thin canonical App UI client integrated in Wave 7. |
| Runtime | Paper/shadow mode | RESOLVED | MISSING | Runtime | J07 | Vertical gate before live operation. |
| Runtime | Live product mode | RESOLVED | MISSING | Runtime/Operations | J08 | Requires explicit operational authorization; roadmap state is not authorization. |
| Interfaces | Authenticated/TLS transport v1 | FROZEN | COMPLETE | API Runtime | J09 | ADR-0063; WSS+mTLS 1.3, exact-fingerprint principal mapping, `j02.market_data.read` scope implemented by PR #310 (Wave 8). |
| Interfaces | Strategy Consumer-API seam v1 | FROZEN | COMPLETE | Application/API | J10 | ADR-0065 §2; in-process `strategy-compose-v1` seam, PR #308 (Wave 8). Not reachable over J02 (DG-K P05). Since DG-K (#381): carried over J02 by `j02-strategy-v1`; opt-in, no host composes it. |
| Interfaces | Replay Consumer-API seam v1 | OPEN_DEFERABLE | MISSING | Application/API | J11 | ADR-0065 §5 explicitly defers: no accepted identity for `HistoricalReplayRuntime`'s `feature_provider` callable yet. Not planned (owner decision 2026-10-10, DG-K #318): replay runs on the deck over K12/K13. |
| Interfaces | Validation Consumer-API seam v1 | FROZEN | COMPLETE | Application/API | J12 | ADR-0065 §3 + Amendment 1; in-process Validation seam, PR #312 (Wave 8). Not reachable over J02 (DG-K P05). Since DG-K (#381): carried over J02 by `j02-validation-v1`; opt-in, no host composes it. |
| Interfaces | Training Consumer-API seam v1 | FROZEN | COMPLETE | Application/API | J13 | ADR-0065 §4 + Amendment 2; in-process bounded Training seam, PR #315 (Wave 8). Not reachable over J02 (DG-K P05). Since DG-K (#381): carried over J02 by `j02-training-v1`; opt-in, no host composes it. |
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
| Operations | Server-owned admitted-input export/reference v1 | FROZEN | COMPLETE | Operations | K12 | ADR-0064; sealed `AdmittedInputManifestV1`/`admission_id` and delivery states, PR #309 (Wave 8). No physical byte transfer or deck reader. Since DG-K (#381): byte delivery over `j02-admitted-input-v2` and a deck protocol client exist; production authority seams do not. |
| Operations | Governed experiment-result import/registration v1 | FROZEN | COMPLETE | Operations | K13 | ADR-0064 §3; full-bundle refusal and idempotent import, PR #311 (Wave 8). Not one transaction across PostgreSQL and SQLite (DG-K P02). Since DG-K (#381): registration is one PostgreSQL transaction and submission is carried by `j02-result-import-v1`; not composed in a host. |
| Topology | Canonical identity serializer v1 | FROZEN | COMPLETE | Engineering/Application | P01 | ADR-0068 (#319); named versioned byte profiles, no identity changes. Closeout #381: PR #344 (issue #330), `quant_platform.canonical`; byte parity on the pinned identity vectors and the two-`PYTHONHASHSEED` cross-process proof are in `tests/test_canonical_v1.py`. |
| Topology | Runtime store on PostgreSQL v1 | FROZEN | COMPLETE | Operations/Application | P02 | ADR-0068 (#319); `runtime` schema for J03/K12/K13; atomic K13 registration. Closeout #381: PR #349 (issue #331: `runtime` schema, unit-scoped roles, J03/K12/K13 stores on PostgreSQL), PR #350 (issue #332: atomic K13 registration), PR #355 (issue #354: a concurrent identical resubmission is refused). Bound: the confirmation that no production SQLite runtime state exists is operational evidence, not code, and is recorded by the maintainer (#381). |
| Topology | Executable host boundaries v1 | FROZEN | COMPLETE | Engineering | P03 | ADR-0068 (#319); hosts stay in `tools/`, per-host import rules. Closeout #381: PR #345 (issue #333), per-host import rules in `tests/test_package_boundaries_v1.py`; no host moved. |
| Topology | J03 handler registry and dispatch v1 | FROZEN | COMPLETE | Application | P04a | ADR-0068 (#319); closed data-local handler set. Closeout #381: PR #351 (issue #334), `application/job_dispatch.py`. Bound: the registry mechanism is complete; `DATA_LOCAL_JOB_REGISTRY` ships **empty**, so no data-local handler exists yet (each enters through its own implementation issue). |
| Topology | Single J03 worker process v1 | FROZEN | COMPLETE | Runtime | P04b | ADR-0068 (#319); exactly one worker, no broker. Closeout #381: PR #352 (issue #335), `tools/job_worker.py` with the PostgreSQL advisory lock and the process-lifetime lock (ADR-0068 Amendment 1). Bound: with an empty handler registry it has nothing to run. |
| Topology | J02 carriage of synchronous J10/J12/J13 v1 | FROZEN | COMPLETE | API Runtime | P05a | ADR-0069 (#319); wire messages and one scope per operation family. Closeout #381: PR #346 (issue #336, per-family dispatch and scope enforcement before decode), PR #347 (issue #337, `j02-strategy-v1`), PR #348 (issue #338, `j02-validation-v1`), PR #380 (issue #339, `j02-training-v1` with the J13 reconstruction codec of ADR-0069 Amendment 2 and the pre-decode `register` grant). Bound: application-owned codecs and opt-in family handlers proven over a real WSS/mTLS listener in tests; no executable composes them. |
| Topology | J02 submit/status/result for J03 data-local jobs v1 | FROZEN | COMPLETE | API Runtime | P05b | ADR-0069 (#319); requires P04b. Closeout #381: PR #353 (issue #340, `j02-job-v1`) and PR #359 (issue #358, multi-body results). Bound: opt-in handlers, not composed in a host, and the data-local registry is empty. |
| Topology | Deck handoff over J02 v1 | FROZEN | COMPLETE | API Runtime/Operations | P06 | ADR-0069 (#319); K12 byte delivery and K13 submission, deck-side client, feature-provider code identity in the bundle. Closeout #381: PR #356 (issue #341, K12 delivery, now `j02-admitted-input-v2`), PR #361 (issue #342, K13 import and feature-provider identity), PR #366 (issue #343, `clients/deck`), PR #373 (issue #357, delivery-attempt identity, admission-gated resume; ADR-0064 Amendment 1, ADR-0066 Amendment 1, ADR-0069 Amendment 1). Bound: protocol client and server-side transports with test authorities; no production K12 authority, no K13 composition in a host, no deck-side bundle producer, no deployment. |
| Topology | K12 server authority seams v1 | FROZEN | COMPLETE | Application/Operations | S01 | ADR-0072 §1 (accepted); authorized by #389; implemented (closeout #409). `resolve`/`members`/`read_member` over the DataGateway/catalog for `j02-admitted-input-v2`; closed sets of accepted operation and profile identities; expiry policy values; byte and member bounds. Today only test authorities exist. Credit (#409): PR #398 (issue #391), `application/admitted_input_authority.py`: `DatasetAdmittedInputAuthority` over the DataGateway, manifest built from the open scan metadata without reading a record, dataset selector only (`artifact_inputs_unavailable`). Bound: whole-file, in-memory delivery; the bounds were raised by ADR-0072 Amendment 1 (issue #431, PR #432) to 512 MiB per file, 1 GiB and 16 files per admission; `definition_identities` is empty (the server passes no feature identity). |
| Topology | K13 composition v1 | FROZEN | COMPLETE | Application/Operations | S02 | ADR-0072 §2 (accepted); authorized by #389; implemented (closeout #409). Closed output-role registry with `ResultOutputContractV1` (replay roles require `replay_feature_provider`), staging bounds, a `GovernedResultImportService` factory over the `runtime` role. Composed in no host today. Credit (#409): PR #400 (issue #392), `application/result_import_composition.py`: the closed six-role registry, the staging bounds and the service factory over one autocommit connection. Bound: the 8 MiB output bound and the staging bounds were not measured on a real output. |
| Topology | API host composition v1 | FROZEN | COMPLETE | API Runtime | S03 | ADR-0072 §3 (accepted); authorized by #389; implemented (closeout #409). `tools/api_server.py` composes TLS/mTLS (ADR-0063), principals, all DG-K families and the J14 store, with fail-closed startup and per-host import rules. Today the host serves J02 v1 market data only. Credit (#409): PR #405 (issue #395), `application/api_host_composition.py` and `tools/api_server.py`: opt-in families, TLS 1.3 mutual TLS, principals file, fail-closed startup, one shared J14 store; the default invocation is unchanged. Bound: `jobs` is composed without a result resolver (no handler exists). |
| Topology | Credential and trust operations v1 | FROZEN | COMPLETE | Operations | S04 | ADR-0072 §4 (accepted); authorized by #389; implemented (closeout #409). Issuance, rotation and revocation procedure and tooling under `infra/`, fingerprint-to-principal mapping and secret handling, within ADR-0063 §2: no credential service, IAM or bearer tokens. Credit (#409): PR #414 (issue #393), `infra/pki/` and `docs/engineering/DECK_PKI_OPERATIONS.md`: offline CA, server and client certificates, fingerprints, restart-based rotation and revocation. Bound: no CRL or OCSP; the file-mode test is skipped where modes are not enforced and runs on Linux CI. |
| Topology | Deck workload library v1 | FROZEN | COMPLETE | Client/Application | S05 | ADR-0072 §5 (accepted); authorized by #389; implemented (closeout #409). Producer of `GovernedResultBundleV1` (deck code identity, feature-provider identity, input digests), submit-flow glue and commit pinning; `clients/deck/` keeps importing nothing from `quant_platform`. Credit (#409): PR #418 (issue #394), `application/deck_result_bundle.py`: the producer of the K13 bundle from the verified admission, clean tree and server commit required. Bound: the provider identity is attribution evidence, not a server-defined feature definition; a bundle names one admission. |
| Topology | Deployment units and runbook v1 | FROZEN | COMPLETE | Operations | S06 | ADR-0072 §6 (accepted); authorized by #389; implemented (closeout #409). systemd units and environment examples for the API and the worker, the ADR-0057 handoff runbook, the promotion procedure and the server/deck compatibility rule. Credit (#409): PR #421 (issue #396), `infra/systemd/` units and environment examples, `docs/engineering/DECK_HANDOFF_RUNBOOK.md`, `docs/engineering/SERVER_DECK_COMPATIBILITY.md`. Bound: the import accepts a result against its own admission's commit, so an admission made before a promotion stays valid (an operator obligation, recorded); no memory limit on the API unit. |
| Topology | Real-topology proof v1 | FROZEN | COMPLETE | Operations | S07 | ADR-0072 §7 (accepted); authorized by #389; implemented (closeout #409). PostgreSQL + API + worker + deck client over WSS/mTLS: admit, deliver, verify, acknowledge, run, upload, submit and register, plus the fault cases the gate defines. Defines "ready to split". Credit (#409): PR #427 (issue #397), part 1, the hermetic proof `PostgresSplitTopology` (14 tests, run by the CI job of the runtime stores), and PR #428, the record of part 2. Bound: part 2 is one happy path on the smallest real partition (3 122 bytes) executed by the maintainer and reported from the session log, on a pull-request commit; no fault was repeated there and the largest real day (252.8 MiB) was not delivered. The topology is deployable only within the bounds of ADR-0072 Amendment 1; see `docs/engineering/DGK2_SPLIT_TOPOLOGY_PROOF.md`. |
| Topology | First data-local J03 handlers v1 | OPEN_DEFERABLE | MISSING | Application | S08 | DG-K2 (#382/#383, ADR-0072 §9); not authorized. Trigger: the maintainer names a first handler (feature/representation materialization, historical import or catalog maintenance). `DATA_LOCAL_JOB_REGISTRY` ships empty; each handler enters through its own issue citing ADR-0068 §6. |

## Inventory checkpoint — L2/main reconciliation (#444)

Recomputed from the union of the actual DAG rows and checked against expanded
Capability Map rows, preserving both parents' accepted credits. No new atom,
capability or follow-on authorization is created. DG-K/DG-K2 are already on main
(#434/#437); L2 remains on implement/l2 pending its separate final wave promotion.
The [scope verifier](../../SCOPE.md#executable-governance-verification) captures both OIDs.

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
| `DG-K2` Server/deck deployment composition (governance #382, #389, #409; design gate #383, #387; Amendment 1 #431) | COMPLETE within recorded bounds (operator closeout #409) | ADR-0072 `ACCEPTED`, Amendment 1 raises the delivery bounds to 512 MiB per file and 1 GiB per admission. `S01`-`S07` (K12 authority, K13 composition, API host composition, credential and trust operations, deck workload module, deployment units, two-part topology proof) are implemented and merged into `implement/dgk2`; the supervised proof is one happy path on the real server. Deployable only within the bounds; promotion into `main` is the operator's separate PR. `S08` (first data-local handlers) is not authorized. Omega adoption out of scope. |
| `DG-K` Process Topology v1 (governance #318, design gate #319, closeout #381) | COMPLETE within recorded bounds (operator closeout #381) | ADR-0068 and ADR-0069 accepted (#319); P01-P06 implemented and merged into `implement/dgk` (PR #344-#353, #355, #356, #359, #361, #366, #373, #380; amendments ADR-0064 Am.1, ADR-0066 Am.1, ADR-0068 Am.1, ADR-0069 Am.1 and Am.2). Not claimed: any executable that composes the families (`tools/api_server.py` serves J02 v1 only, without TLS/mTLS options), production K12 authority seams, K13 composition in a host, a deck-side bundle producer, credential issuance and deployment runbook, a data-local J03 handler (the registry ships empty), and an end-to-end proof on the real topology. Promotion into `main` is the operator's separate PR; no Golden proof claimed. |
| `DG-C` L2 historical v1 closeout (#442, `implement/l2`) | COMPLETE within v1 bounds | `A14`, `A14a`-`A14e` COMPLETE (PR #375/#379/#401/#411/#417/#408); golden #413/#430 accepted, 11/11 at `1cdd78f`. ADR-0070 Amendments 1-2; ADR-0049 Amendment 1 (#424): one redundant copy only, never the last. #425/#426 credited as operational tool, no A14 atom or new K09 credit. `A14g` held; `A14f` gate released by #448 (MISSING); separate promotion to `main` after DG-K. [Limits and eight triggers, none authorized](../architecture/OPEN_DECISIONS.md#l2-v1-reopening-triggers-442). |
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

Wave 8 is closed (#327), with its original credit bounds and no additional
Golden proof. DG-K P01-P06 (#381) and DG-K2 S01-S07 (#409) are COMPLETE within
their row bounds and already on main (#434/#437). DG-K2 supplies the executable
composition that DG-K's library closeout did not claim; its supervised proof
remains one happy path on the smallest real partition, within ADR-0072 Amendment 1
bounds and the recorded findings. S08 is not authorized; J11 is not planned.

L2 v1 is CLOSED on implement/l2 (#442/#443), A14 and A14a-A14e COMPLETE,
golden #413/#430 accepted 11/11 with explicit gaps. A14g remains held and A14f's start gate was
released by #448 (MISSING);
no live L2, ob500, population guarantee or gap-free UTC-midnight L2 is claimed.
Reconciliation #444 combines both histories before a separate L2 promotion.
J07/J08, RL, broker/live execution, second venue and L1/L3 remain outside those
closed scopes. Frontier/readiness state is **not concurrent implementation
authorization**. The historical gate rows above record each closeout's bounds;
later DG-K2 credits do not broaden the earlier proof.

See [`ROADMAP.md`](ROADMAP.md) for macro progression and [`CAPABILITY_DAG.md`](CAPABILITY_DAG.md) for exact dependency/decision-gate semantics.
