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
| Data | Live trades acquisition | OPEN_BLOCKING | MISSING | Data Plane | A11 | DG-B live branch + relevant DG-H prerequisites; implemented checkpoint/recovery follows live acquisition. |
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
| Access | Live access/cursor | OPEN_BLOCKING | MISSING | Data Access | B06 | DG-B live branch only. |
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
| Representation | Incremental/live Candle computation | FROZEN | MISSING | Representation | D04 | Runtime blocked by live access implementation, not by Candle semantics. |
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
| Research | OutcomeSpec/Outcome | RESOLVED | COMPLETE | Research | F03 | Future-window identity and availability separated from labels; implemented, issue #86/PR #90. |
| Research | Event studies/sweeps | RESOLVED | COMPLETE | Research | F04 | Reproducible study population/aggregates; implemented, issue #92/PR #94. |
| Validation | Walk-forward schedule | RESOLVED | COMPLETE | Validation | F05 | Deterministic pure temporal atom. |
| Validation | Availability/purge/embargo | FROZEN | COMPLETE | Validation | F06 | ADR-0031. |
| Validation | Labels/censoring/lockbox | FROZEN | MISSING | Validation | F07 | ADR-0036; outcome-derived labels/censoring/terminal-lockbox semantics frozen; implementation pending. |
| Validation | DSR/PBO | OPEN_BLOCKING | MISSING | Research/Validation | F08 | Separate DG-E DSR/PBO branch; does not block Strategy/ML paths that depend on F07. |
| Strategy | StrategySpec/DecisionIntent | RESOLVED | MISSING | Strategy | G01 | Strategy remains upstream of execution. |
| Strategy | Policy composition | RESOLVED | MISSING | Strategy | G02 | Deterministic composition required. |
| Strategy | Risk/sizing | RESOLVED | MISSING | Strategy | G03 | No client-owned logic. |
| Strategy | Session/cooldown semantics | OPEN_BLOCKING | MISSING | Strategy | G04 | DG-F. |
| Execution | Order/Fill lifecycle | RESOLVED | MISSING | Execution | H01 | Explicit state transitions. |
| Execution | Cost/synthetic-fill model | RESOLVED | MISSING | Execution | H02 | Model version is provenance. |
| Execution | Conflict/partial-fill semantics | OPEN_BLOCKING | MISSING | Execution | H03 | DG-F. |
| Portfolio | Portfolio/ledger | RESOLVED | MISSING | Portfolio | H04 | Deterministic accounting. |
| Execution | Deterministic replay | RESOLVED | MISSING | Execution | H05 | Uses canonical data/access; no storage bypass. |
| Portfolio | Multi-asset execution | OPEN_DEFERABLE | MISSING | Portfolio | H06 | Wait for concrete product scope. |
| Experiments | Study/Trial/Run/Artifact semantic model | RESOLVED | COMPLETE | Experiment System | I01 | One canonical experiment identity family. |
| Experiments | Canonical experiment persistence | RESOLVED | COMPLETE | Experiment System | I02 | One restart-safe canonical persistence model. |
| Experiments | Trial accounting/comparison | RESOLVED | MISSING | Experiment System | I03 | Requires persistence/replay. |
| ML | Supervised input/selection | RESOLVED | MISSING | Learning | I04 | Depends on Feature + Validation semantics. |
| ML | Supervised training/evaluation | RESOLVED | MISSING | Learning | I05 | Binds code/data/splits/model/metrics. |
| RL | Strategic RL contract | OPEN_BLOCKING | MISSING | Strategic RL | I06 | DG-G strategic-RL branch. |
| RL | Execution RL contract | OPEN_BLOCKING | MISSING | Execution RL | I07 | DG-G execution-RL branch; structurally separate from strategic RL. |
| Interfaces | Consumer API semantic boundary | FROZEN | COMPLETE | Application/API | J01 | ADR-0020; semantic API is not a DataGateway wrapper and does not imply transport runtime. |
| Interfaces | Canonical API transport | OPEN_DEFERABLE | MISSING | API Runtime | J02 | Triggered by real remote/client need. |
| Runtime | Job runtime | OPEN_BLOCKING | MISSING | Runtime | J03 | DG-G job branch; durable identity/retry/result semantics. |
| Clients | CLI | RESOLVED | MISSING | Client Layer | J04 | Thin canonical client. |
| Clients | TUI | RESOLVED | MISSING | Client Layer | J05 | Thin canonical client. |
| Clients | App UI | RESOLVED | MISSING | Client Layer | J06 | Thin canonical client. |
| Runtime | Paper/shadow mode | RESOLVED | MISSING | Runtime | J07 | Vertical gate before live operation. |
| Runtime | Live product mode | RESOLVED | MISSING | Runtime/Operations | J08 | Requires explicit operational authorization; roadmap state is not authorization. |
| Operations | Provisioning/fixtures/CI | RESOLVED | COMPLETE | Engineering | K01 | Repository validation foundation complete. |
| Operations | Server/runtime identity | OPEN_BLOCKING | MISSING | Operations | K02 | DG-H identity branch. |
| Operations | Observability | RESOLVED | COMPLETE | Operations | K03 | Minimum externally observable health/provenance/failure transitions. |
| Operations | Capacity observation | RESOLVED | COMPLETE | Operations | K04 | Observational only. |
| Operations | Health/pressure policy | FROZEN | COMPLETE | Operations | K05 | ADR-0028. |
| Operations | RAW/source protection | FROZEN | COMPLETE | Operations/Data Plane | K06 | ADR-0032; attributable-evidence protection-identity/assessment seam, no backup dependency. |
| Operations | Tier relocation | OPEN_BLOCKING | MISSING | Operations/Data Plane | K07 | DG-H relocation branch; sibling of backup after source protection. |
| Operations | Backup/restore proof | OPEN_BLOCKING | MISSING | Operations | K08 | DG-H backup branch; depends on K06, not K07; restore proof precedes deletion authority. |
| Operations | Retention/deletion authority | OPEN_BLOCKING | MISSING | Operations | K09 | DG-H deletion branch. |
| Operations | Checkpoint/recovery | OPEN_BLOCKING | MISSING | Operations/Data Plane | K10 | DG-H recovery branch; implementation follows A11 and requires K03/K08. |
| Governance | Governance-state consistency | RESOLVED | COMPLETE | Governance | K11 | Roadmap vNext authority set remains coherent after post-C03 reconciliation. |

## Gate / evidence state

| Capability | State | Notes |
|---|---|---|
| Contract Freeze Gate | PASSED | ADR-0023 accepted. |
| Conformity Implementation Gate | PASSED | All ADR-0023 exit criteria satisfied. |
| Human Golden Bybit BTCUSDT E2E | PASS | Exact accepted reference vertical. |
| Package Boundary / Modular Monolith Foundation v1 | COMPLETE | Ownership/dependency enforcement established. |
| Legacy Capability Harvest Audit v1 | COMPLETE | 31 capabilities classified; H01 selected as harvest candidate, H14 retained as REVIEW. |
| ASS-01 Application ownership/enforcement | COMPLETE | PR #27 integrated. |
| ASS-02 semantic selector resolution (C02) | COMPLETE | PR #30 integrated; reviewed exact-head CI passed. |
| ASS-02 result/error translation (C03) | COMPLETE | PR #41 integrated reviewed head `f382a2e6...`; integrity #91 passed on exact head. |
| ASS-02 in-process Application vertical | COMPLETE | C02 + C03 complete; does not imply C05/C04/API/jobs/clients. |
| K11 Governance-state consistency | COMPLETE | Canonical authority set reconciled after C03 integration. |
| E02 FeatureDefinition v1 semantic foundation | COMPLETE | ADR-0026 accepted; immutable runtime model and targeted tests implemented. |
| D06 FootprintDefinition v1 representation foundation | COMPLETE | ADR-0027 accepted; immutable historical FINAL Footprint v1 runtime implemented. |
| B04 Non-contiguous coverage reads v1 | COMPLETE | ADR-0029 accepted; explicit `ALLOW_PARTIAL` DataGateway policy implemented. |
| K06 RAW/source protection v1 | COMPLETE | ADR-0032 accepted; attributable-evidence `SafetyRelevanceAssertion` pattern implemented. |
| A10 Backfill/repair v1 | COMPLETE | ADR-0033 accepted; two known limitations tracked (coverage-trigger re-verification, partial candidate identity binding). |
| E04 FeatureArtifact v1 | COMPLETE | ADR-0034 accepted; `SupportShape`, FINAL-only sealing, `InitVar` construction guard, exact-Fraction equivalence. |
| E06 H01 canonical integration v1 | COMPLETE | ADR-0035 accepted; bounded implementation completed, issue #83/PR #88. |
| F02 EventSpec/detection v1 | COMPLETE | Traceable event rule + availability evidence implemented, issue #85/PR #89. |
| F03 OutcomeSpec/Outcome v1 | COMPLETE | Horizon/censoring/availability explicit, implemented, issue #86/PR #90. |
| F04 Event studies/sweeps v1 | COMPLETE | Reproducible study population/aggregates and parameter sweep runtime implemented, issue #92/PR #94. |
| F07 labels/censoring/lockbox v1 | FROZEN | ADR-0036 accepted under issue #95; implementation remains MISSING. |
| Wave 1 (`implement/wave-1`, issues #51-#77) | CONCLUDED | 2026-09-19; issue #73's Human Golden E2E closeout (1440-candle 1m D03, official `GOLDEN E2E: PASS`) is closed and credited. |
| Wave 2 (`implement/wave-2`, issues #83,#85,#86) | COMPLETE | E06/F02/F03 merged (PR #88/#89/#90); reconciled by issue #87. |

## Current frontier

F07 label/censoring/lockbox semantics are **FROZEN** under ADR-0036; implementation is `MISSING` and its declared dependencies F03/F06 are `COMPLETE`.

```text
F07 implementation
DG-E / F08 decision branch (independent)
```

The next implementation-ready atom on the Validation -> Strategy/ML path is F07. It requires a separately bounded implementation issue/scope before runtime mutation.

F08 DSR/PBO remains `OPEN_BLOCKING` in the independent robust-comparison branch. Resolving F07 does not activate or resolve F08.

Frontier membership means planning dependencies and decision gates are satisfied; it does not authorize implementation.

See [`ROADMAP.md`](ROADMAP.md) for macro progression and [`CAPABILITY_DAG.md`](CAPABILITY_DAG.md) for exact dependency/decision-gate semantics.
