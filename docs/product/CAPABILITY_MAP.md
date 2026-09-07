# Capability Map

This is the compact canonical-state snapshot. Atom-level dependencies, acceptance propositions, decision gates and execution readiness are authoritative in [`CAPABILITY_DAG.md`](CAPABILITY_DAG.md). Legacy evidence is reference evidence only.

Decision state and implementation state are intentionally separate.

| Domain | Capability | Decision State | Implementation State | Target owner | Atom(s) | Notes |
|---|---|---|---|---|---|---|
| Governance | Repository/governance foundation | RESOLVED | READY | Engineering/Governance | K01,K11 | Canonical repository, CI/fixtures and governance model exist; state-consistency cleanup remains bounded governance work. |
| Data | `trade-v1` canonical record | FROZEN | COMPLETE | Data Plane | A01 | Frozen schema/identity/ordering semantics. |
| Data | Dataset/partition identity | FROZEN | COMPLETE | Data Plane | A02 | `dataset-manifest-v1` retained; v2 additive for source-acquired topology. |
| Data | Source-acquired lineage v2 | FROZEN | COMPLETE | Data Plane | A03 | No synthetic parent; source provenance remains evidence-owned. |
| Data | Declared coverage | FROZEN | COMPLETE | Data Plane | A04 | Coverage is explicit and distinct from observed row bounds. |
| Data | Catalog/schema bootstrap | RESOLVED | COMPLETE | Data Plane/Engineering | A05 | Repository schema bytes authoritative; conflicts fail closed. |
| Data | Canonical Parquet materialization | FROZEN | COMPLETE | Producer | A06 | First historical vertical proven. |
| Data | Historical Bybit acquisition | FROZEN | PARTIAL | Source/Producer | A07 | Narrow reference vertical exists; not a general ingest runtime. |
| Data | S13 certification | FROZEN | COMPLETE | Producer | A08 | Durable authoritative quality evidence. |
| Data | S14 publication/eligibility | FROZEN | COMPLETE | Producer | A09 | First vertical catalog publication proven. |
| Data | Backfill/repair | OPEN_BLOCKING | MISSING | Data Plane | A10 | DG-B: reconciliation, retry and precedence semantics must be resolved before implementation. |
| Data | Live trades acquisition | OPEN_BLOCKING | MISSING | Data Plane | A11 | DG-B + relevant DG-H prerequisites. |
| Data | Second-venue trades | OPEN_DEFERABLE | MISSING | Data Plane | A12 | Triggered by real second-provider evidence; do not design a generic resolver early. |
| Data | L1 contract/acquisition | OPEN_BLOCKING | MISSING | Data Plane | A13 | DG-C, triggered by a concrete feed. |
| Data | L2 contract/acquisition | OPEN_BLOCKING | MISSING | Data Plane | A14 | DG-C, triggered by a concrete feed. |
| Data | L3/MBO contract | OPEN_DEFERABLE | MISSING | Data Plane | A15 | Explicitly wait for a real L3 feed. |
| Data | General quality lifecycle | OPEN_BLOCKING | PARTIAL | Data Plane | A16 | First vertical frozen; general lifecycle belongs to DG-B. |
| Access | Data Access owner/boundary | RESOLVED | COMPLETE | Data Access | B01 | Canonical `quant_platform.access`; dependency direction mechanically enforced. |
| Access | Bounded historical scan | FROZEN | COMPLETE | Data Access | B02,B07 | `DataGateway.scan()` is the canonical bounded historical seam. |
| Access | Result identity/provenance | FROZEN | COMPLETE | Data Access | B03 | Semantic/source identity distinct from physical locator. |
| Access | Non-contiguous coverage read | OPEN_BLOCKING | MISSING | Data Access | B04 | DG-B. |
| Access | Durable DatasetSnapshot | OPEN_DEFERABLE | MISSING | Data Access | B05 | Shape waits for a concrete durable-replay requirement. |
| Access | Live access/cursor | OPEN_BLOCKING | MISSING | Data Access | B06 | DG-B. |
| Access | Schema evolution beyond accepted versions | OPEN_DEFERABLE | MISSING | Data Access | B08 | Resolve against real next-version evidence. |
| Application | ASS-01 ownership/enforcement | RESOLVED | COMPLETE | Application | C01 | `quant_platform.application` is the canonical in-process composition owner; PR #27 merged. |
| Application | ASS-02 semantic selector resolution | FROZEN | MISSING | Application | C02 | Current execution frontier; no transport required. |
| Application | ASS-02 result/error translation | FROZEN | MISSING | Application | C03 | Follows C02 and frozen Consumer API semantics. |
| Application | ASS-03 tool convergence | RESOLVED | MISSING | Application | C04 | Finite exact-edge orchestration debt; requires real application service target. |
| Application | Configuration convergence | OPEN_BLOCKING | MISSING | Application/Engineering | C05 | DG-D; one convention only, no speculative DI framework. |
| Application | Multi-capability resolver | OPEN_DEFERABLE | MISSING | Application | C06 | Triggered by a real second venue/representation. |
| Representation | Representation identity | RESOLVED | PARTIAL | Representation | D01 | Distinct from DatasetIdentity. |
| Representation | CandleDefinition v1 | FROZEN | COMPLETE | Representation | D02 | Accepted semantic contract; runtime is a separate capability. |
| Representation | Historical Candle computation | FROZEN | MISSING | Representation | D03 | Current execution frontier; on-demand CLOSED candles. |
| Representation | Incremental/live Candle computation | FROZEN | MISSING | Representation | D04 | Runtime blocked by live access implementation, not by Candle semantics. |
| Representation | Candle materialization identity | OPEN_BLOCKING | MISSING | Representation | D05 | DG-A. |
| Representation | Footprint representation | OPEN_BLOCKING | MISSING | Representation | D06 | DG-A: canonical grain/tick-grid/adjacency/availability. |
| Features | Definition/set/artifact separation | FROZEN | COMPLETE | Feature Engine | E01 | Architectural identity separation accepted. |
| Features | FeatureDefinition v1 | OPEN_BLOCKING | MISSING | Feature Engine | E02 | DG-A. |
| Features | FeatureSet catalog/provider | RESOLVED | PARTIAL | Feature Engine | E03 | Existing catalog foundation retained. |
| Features | FeatureArtifact/materialization | OPEN_BLOCKING | MISSING | Feature Engine | E04 | DG-A. |
| Features | H01 pure imbalance kernel | RESOLVED | MISSING | Feature Engine | E05 | Current execution frontier; narrow Legacy Harvest ADOPT boundary only. |
| Features | H01 canonical integration | OPEN_BLOCKING | MISSING | Feature Engine | E06 | Requires D06/E02/E04; DG-A. |
| Features | Generic provider extension | OPEN_DEFERABLE | MISSING | Feature Engine | E07 | Wait for a real second provider. |
| Research | HypothesisSpec | RESOLVED | MISSING | Research | F01 | Depends on FeatureDefinition for the canonical vertical. |
| Research | EventSpec/detection | RESOLVED | MISSING | Research | F02 | Requires FeatureArtifact for the full vertical. |
| Research | OutcomeSpec/Outcome | RESOLVED | MISSING | Research | F03 | Future-window identity and availability separated from labels. |
| Research | Event studies/sweeps | RESOLVED | MISSING | Research | F04 | Reproducible study population/aggregates. |
| Validation | Walk-forward schedule | RESOLVED | MISSING | Validation | F05 | Current execution frontier; deterministic pure temporal atom. |
| Validation | Availability/purge/embargo | OPEN_BLOCKING | MISSING | Validation | F06 | DG-E. |
| Validation | Labels/censoring/lockbox | OPEN_BLOCKING | MISSING | Validation | F07 | DG-E. |
| Validation | DSR/PBO | OPEN_BLOCKING | MISSING | Research/Validation | F08 | H14 REVIEW residue; blocks only its dependent evaluation capability until activated. |
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
| Experiments | Study/Trial/Run/Artifact semantic model | RESOLVED | MISSING | Experiment System | I01 | Current execution frontier. |
| Experiments | Canonical experiment persistence | OPEN_BLOCKING | MISSING | Experiment System | I02 | DG-G; avoid competing persistence. |
| Experiments | Trial accounting/comparison | RESOLVED | MISSING | Experiment System | I03 | Requires persistence/replay. |
| ML | Supervised input/selection | RESOLVED | MISSING | Learning | I04 | Depends on Feature + Validation semantics. |
| ML | Supervised training/evaluation | RESOLVED | MISSING | Learning | I05 | Binds code/data/splits/model/metrics. |
| RL | Strategic RL contract | OPEN_BLOCKING | MISSING | Strategic RL | I06 | DG-G; task-specific state/action/reward. |
| RL | Execution RL contract | OPEN_BLOCKING | MISSING | Execution RL | I07 | DG-G; structurally separate from strategic RL. |
| Interfaces | Consumer API semantic boundary | FROZEN | COMPLETE | Application/API | J01 | ADR-0020; semantic API is not a DataGateway wrapper and does not imply transport runtime. |
| Interfaces | Canonical API transport | OPEN_DEFERABLE | MISSING | API Runtime | J02 | Triggered by real remote/client need. |
| Runtime | Job runtime | OPEN_BLOCKING | MISSING | Runtime | J03 | DG-G; durable identity/retry/result semantics. |
| Clients | CLI | RESOLVED | MISSING | Client Layer | J04 | Thin canonical client. |
| Clients | TUI | RESOLVED | MISSING | Client Layer | J05 | Thin canonical client. |
| Clients | App UI | RESOLVED | MISSING | Client Layer | J06 | Thin canonical client. |
| Runtime | Paper/shadow mode | RESOLVED | MISSING | Runtime | J07 | Vertical gate before live operation. |
| Runtime | Live product mode | RESOLVED | MISSING | Runtime/Operations | J08 | Requires explicit operational authorization; roadmap state is not authorization. |
| Operations | Provisioning/fixtures/CI | RESOLVED | COMPLETE | Engineering | K01 | Repository validation foundation ready. |
| Operations | Server/runtime identity | OPEN_BLOCKING | MISSING | Operations | K02 | DG-H. |
| Operations | Observability | OPEN_BLOCKING | MISSING | Operations | K03 | DG-H. |
| Operations | Capacity observation | RESOLVED | MISSING | Operations | K04 | Current execution frontier; observational only. |
| Operations | Health/pressure policy | OPEN_BLOCKING | MISSING | Operations | K05 | DG-H. |
| Operations | RAW/source protection | OPEN_BLOCKING | MISSING | Operations/Data Plane | K06 | DG-H. |
| Operations | Tier relocation | OPEN_BLOCKING | MISSING | Operations/Data Plane | K07 | DG-H. |
| Operations | Backup/restore proof | OPEN_BLOCKING | MISSING | Operations | K08 | DG-H; restore proof precedes deletion authority. |
| Operations | Retention/deletion authority | OPEN_BLOCKING | MISSING | Operations | K09 | DG-H. |
| Operations | Checkpoint/recovery | OPEN_BLOCKING | MISSING | Operations/Data Plane | K10 | DG-H. |

## Gate / evidence state

| Capability | State | Notes |
|---|---|---|
| Contract Freeze Gate | PASSED | ADR-0023 accepted. |
| Conformity Implementation Gate | PASSED | All ADR-0023 exit criteria satisfied. |
| Human Golden Bybit BTCUSDT E2E | PASS | Exact accepted reference vertical. |
| Package Boundary / Modular Monolith Foundation v1 | COMPLETE | Ownership/dependency enforcement established. |
| Legacy Capability Harvest Audit v1 | COMPLETE | 31 capabilities classified; H01 selected as harvest candidate, H14 retained as REVIEW. |
| ASS-01 Application ownership/enforcement | COMPLETE | PR #27 integrated. |

## Current frontier

```text
C02  D03  E05  F05  I01  K04  K11
```

Frontier membership means planning dependencies are satisfied; it does not authorize implementation.

See [`ROADMAP.md`](ROADMAP.md) for macro progression and [`CAPABILITY_DAG.md`](CAPABILITY_DAG.md) for exact dependency/decision-gate semantics.
