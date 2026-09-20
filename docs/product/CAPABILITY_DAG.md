# Quant Platform Capability DAG vNext

Status: **CANONICAL; post-F08 semantic freeze (Wave 3)**.

This document is the execution/dependency view of the Quant Platform roadmap. `ROADMAP.md` remains the human-readable macro progression; `CAPABILITY_MAP.md` remains the compact current-state view; accepted ADRs/contracts remain semantic authority.

## Planning model

Roadmap readiness is not the same thing as freezing every future semantic choice.

An atom is roadmap-defined when its observable outcome, owner, dependencies, unlocks, acceptance proposition and decision state are classified, and any unresolved decision is either assigned to an atom-specific blocking gate or has an explicit deferable evidence trigger.

Current audited inventory after F08 semantic freeze (ADR-0037 / issue #97; F07 frozen by ADR-0036 / issue #95; F04 merged via issue #92, PR #94 on `implement/wave-3`; post-Wave-2 atoms E06/F02/F03 merged via issues #83/#85/#86, PRs #88/#89/#90; K06/A10/E04/C05/D03/E05/F05/I01/K04/F01/K05/I02/K03/F06 implementation previously confirmed against merged PRs):

```text
TOTAL_ATOMS                     = 87
CLASSIFIED_ATOMS                = 87
UNCLASSIFIED_GAPS               = 0
SEMANTIC_FROZEN_OR_RESOLVED     = 64 / 87 = 73.6%
OPEN_BLOCKING                   = 15
OPEN_DEFERABLE                  = 8
ROADMAP_DEFINED                 = 64 + 15 + 8 = 87
ROADMAP_PLANNING_COMPLETENESS   = 87 / 87 = 100%
IMPLEMENTATION_COMPLETE         = 43 / 87 = 49.4%
```

The 100% planning score does **not** mean 100% of future semantics are frozen. It means every atom is classified and every unresolved proposition has a bounded activation rule. The 73.6% semantic-freeze/resolution score must not be increased by prematurely deciding second-provider, live, RL, transport or operational semantics.

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
| A07 | Bybit historical acquisition | Source/Producer | A01 | A08,K06 | FROZEN | PARTIAL | Reference vertical proven; Ingest/ADR-0023 |
| A08 | S13 certification | Producer | A03,A04,A06,A07 | A09,A16 | FROZEN | COMPLETE | Durable fail-closed evidence; ADR-0023 |
| A09 | S14 publication | Producer | A05,A08 | B02,A10 | FROZEN | COMPLETE | Eligible partition becomes readable; ADR-0023 |
| A10 | Backfill and repair | Data Plane | A09,A16 | A11 | FROZEN | COMPLETE | Deterministic repair-intent/candidate/cutover; ADR-0033 |
| A11 | Live trades acquisition | Data Plane | A10,K05,K06,K08 | B06,K10,J07 | OPEN_BLOCKING | MISSING | Restart/overlap/duplicate invariants; DG-B live branch + relevant DG-H |
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
| B06 | Live access/cursor | Data Access | A11,B03 | D04,J07 | OPEN_BLOCKING | MISSING | Deterministic resume/replay; DG-B live branch |
| B07 | In-process batch surface | Data Access | B02 | C02,D03 | RESOLVED | COMPLETE | Existing bounded `DataScan`; concrete naming local |
| B08 | Schema evolution policy | Data Access | new schema evidence | A13-A15 | OPEN_DEFERABLE | MISSING | Resolve when a new real schema/version appears |

### C — Application

| ID | Capability | Owner | Requires | Unlocks | Decision | Impl | Acceptance / authority |
|---|---|---|---|---|---|---|---|
| C01 | Application ownership | Application | B01 | C02-C05 | RESOLVED | COMPLETE | `quant_platform.application`; ASS-01/ADR-0024 |
| C02 | ASS-02 semantic selector resolution | Application | C01,B02,D01,J01 | C03 | FROZEN | COMPLETE | Semantic request resolves without storage identity; Consumer API |
| C03 | ASS-02 result/error translation | Application | C02 | C04,J02,J04-J06 | FROZEN | COMPLETE | PR #41; stable envelope/error semantics; exact-head integrity #91 PASS |
| C04 | Tool orchestration convergence | Application | C02,C03,C05 | governed entry points | RESOLVED | COMPLETE | Exact ASS-03 debt reaches zero; issue #40/PR #44, closed 2026-09-14; `TOOLS_PENDING_ASS03`/`TOOLS_TESTS_PENDING_ASS03` empty and `test_executable_orchestration_respects_the_application_seam` passing since that commit |
| C05 | Configuration convergence | Engineering/Application | C01 | C04,K02 | RESOLVED | COMPLETE | CLI > env > declared default > fail; typed immutable capability-specific config; Application owns composition |
| C06 | Multi-capability resolution | Application | second venue/representation evidence | general application service | OPEN_DEFERABLE | MISSING | Unsupported combinations explicit; no speculative registry |

### D — Representation

| ID | Capability | Owner | Requires | Unlocks | Decision | Impl | Acceptance / authority |
|---|---|---|---|---|---|---|---|
| D01 | Representation identity | Representation | — | C02,D02,E02 | RESOLVED | PARTIAL | Separate from DatasetIdentity; Consumer/Core |
| D02 | CandleDefinition v1 | Representation | A01,D01 | D03,D04 | FROZEN | COMPLETE | Accepted definition/hash/schema semantics; ADR-0021 |
| D03 | Historical candle computation | Representation | B02,D02 | D05,E03 | FROZEN | COMPLETE | Reproducible CLOSED candles on demand |
| D04 | Incremental/live candles | Representation | B06,D02 | J07 | FROZEN | MISSING | PARTIAL converges to historical-equivalent CLOSED output |
| D05 | Candle materialization identity | Representation | D02,D03,A02,A03 | research reuse | OPEN_BLOCKING | MISSING | Persisted series binds definition/source/support; DG-A candle-materialization branch |
| D06 | Footprint representation | Representation | A01,D01 | E06 | FROZEN | COMPLETE | FootprintDefinition v1 exact duration/tick grid/sparse levels/finality; ADR-0027 |

### E — Feature

| ID | Capability | Owner | Requires | Unlocks | Decision | Impl | Acceptance / authority |
|---|---|---|---|---|---|---|---|
| E01 | Definition/set/artifact separation | Feature Engine | D01 | E02-E04 | FROZEN | COMPLETE | Distinct lifecycle identities; ADR-0016 |
| E02 | FeatureDefinition v1 | Feature Engine | E01,D01 | E03,E04,E06 | FROZEN | COMPLETE | Versioned semantics + availability + identity; ADR-0026 |
| E03 | FeatureSet catalog/provider | Feature Engine | E01,E02 | E04 | RESOLVED | PARTIAL | Ordered provider resolution; ADR-0016/CM |
| E04 | FeatureArtifact/materialization | Feature Engine | E02,E03,A02 | F01,I04,E06 | FROZEN | COMPLETE | Deterministic identity, SupportShape, FINAL-only sealing; ADR-0034 |
| E05 | H01 pure imbalance kernel | Feature Engine | Legacy Harvest audit | E06 | RESOLVED | COMPLETE | Narrow ADOPT numeric/edge semantics |
| E06 | H01 canonical integration | Feature Engine | D06,E02,E04,E05 | H01-backed research | FROZEN | COMPLETE | ADR-0035; two direct Footprint-derived observables, exact observation universe/provenance, one `h01_imbalance@1` FeatureSet; implemented, issue #83/PR #88 |
| E07 | Custom/provider extension | Feature Engine | E02,real second-provider need | broader library | OPEN_DEFERABLE | MISSING | Let second provider drive minimum extension seam |

### F — Research & Evaluation

| ID | Capability | Owner | Requires | Unlocks | Decision | Impl | Acceptance / authority |
|---|---|---|---|---|---|---|---|
| F01 | HypothesisSpec | Research | E02 | F02,F03 | RESOLVED | COMPLETE | Reproducible hypothesis references canonical observables |
| F02 | EventSpec/detection | Research | F01,E04 | F04 | RESOLVED | COMPLETE | Traceable event rule + availability evidence; H01-backed rules additionally consume E06 outputs; implemented, issue #85/PR #89 |
| F03 | OutcomeSpec/Outcome | Research | F01 | F04,F07 | RESOLVED | COMPLETE | Horizon/censoring/availability explicit; implemented, issue #86/PR #90 |
| F04 | Event study/sweeps | Research | F02,F03,B03 | I01,F08 | RESOLVED | COMPLETE | Re-run preserves population and aggregates; implemented, issue #92/PR #94 |
| F05 | Walk-forward schedule | Validation | shared time primitives | F06,F08 | RESOLVED | COMPLETE | Deterministic temporal fold boundaries |
| F06 | Availability/purge/embargo | Validation | F05,E02 | F07,I04 | FROZEN | COMPLETE | Adversarial leakage rejected; ADR-0031 |
| F07 | Labels/censoring/lockbox | Validation | F03,F06 | G01,I04 | FROZEN | MISSING | Outcome-derived labels, exact target support and terminal hidden-evaluation lockbox; ADR-0036 |
| F08 | DSR/PBO | Research/Validation | F04,F05 | robust comparison | FROZEN | MISSING | DSR-L + full CSCV PBO, explicit comparable panel/numerical policy and pinned vectors; ADR-0037 |

### G — Strategy & Decision

| ID | Capability | Owner | Requires | Unlocks | Decision | Impl | Acceptance / authority |
|---|---|---|---|---|---|---|---|
| G01 | StrategySpec/DecisionIntent | Strategy | F07 | G02,H01 | RESOLVED | MISSING | Same inputs/config => same intent/provenance |
| G02 | Policy composition | Strategy | G01 | G03,G04 | RESOLVED | MISSING | Deterministic ordering/conflicts |
| G03 | Risk/sizing | Strategy | G01,G02 | H01 | RESOLVED | MISSING | Reproducible limits/sizing evidence |
| G04 | Session/cooldown semantics | Strategy | G02,F06 | H01,H03 | OPEN_BLOCKING | MISSING | DST/session/cooldown vectors; DG-F |

### H — Execution & Portfolio

| ID | Capability | Owner | Requires | Unlocks | Decision | Impl | Acceptance / authority |
|---|---|---|---|---|---|---|---|
| H01 | Order/Fill lifecycle | Execution | G03,G04 | H02,H04 | RESOLVED | MISSING | Illegal transitions rejected |
| H02 | Cost/synthetic-fill model | Execution | H01 | H04,H05 | RESOLVED | MISSING | Pinned fee/slippage/fill scenarios |
| H03 | Execution conflict model | Execution | H01,G04 | H04,H05 | OPEN_BLOCKING | MISSING | Same-bar/OCO/partial-fill single result; DG-F |
| H04 | Portfolio/ledger | Portfolio | H01,H02,H03 | H05,I03 | RESOLVED | MISSING | Accounting identities hold across replay |
| H05 | Deterministic replay | Execution | H02,H04 | J07,I03 | RESOLVED | MISSING | Replay identity binds data/strategy/execution model |
| H06 | Multi-asset execution | Portfolio | H04,real product need | advanced strategy/RL | OPEN_DEFERABLE | MISSING | Resolve on concrete multi-asset scope |

### I — Experiment / ML / RL

| ID | Capability | Owner | Requires | Unlocks | Decision | Impl | Acceptance / authority |
|---|---|---|---|---|---|---|---|
| I01 | Study/Trial/Run/Artifact semantic model | Experiment System | Core concepts | I02,I03 | RESOLVED | COMPLETE | One canonical experiment identity family |
| I02 | Experiment persistence | Experiment System | I01,A05 | I03,J03 | RESOLVED | COMPLETE | One restart-safe canonical persistence model |
| I03 | Trial accounting/comparison | Experiment System | I01,I02,H05 | model selection | RESOLVED | MISSING | Resume idempotent; comparable population identity |
| I04 | Supervised input/selection | Learning | E04,F06,F07 | I05 | RESOLVED | MISSING | Durable split/provenance/anti-leakage evidence |
| I05 | Supervised training/evaluation | Learning | I03,I04 | J07 | RESOLVED | MISSING | Run binds code/data/splits/model/metrics |
| I06 | Strategic RL contract | Strategic RL | G01,F07,I01 | RL runtime | OPEN_BLOCKING | MISSING | State/action/reward excludes execution-control task; DG-G strategic-RL branch |
| I07 | Execution RL contract | Execution RL | H01-H05,I01 | RL runtime | OPEN_BLOCKING | MISSING | Structurally separate execution task; DG-G execution-RL branch |

### J — Runtime & Interface

| ID | Capability | Owner | Requires | Unlocks | Decision | Impl | Acceptance / authority |
|---|---|---|---|---|---|---|---|
| J01 | Consumer API semantics | Application/API | D01 | C02,J02 | FROZEN | COMPLETE | Stable semantic selector/result/error boundary; ADR-0020 |
| J02 | Canonical API transport | API Runtime | C03,real client need | J04-J06 | OPEN_DEFERABLE | MISSING | Preserve Consumer API semantics; transport intentionally deferred |
| J03 | Job runtime | Runtime | C03,I02 | long operations | OPEN_BLOCKING | MISSING | Durable identity/retry/result semantics; DG-G job branch |
| J04 | CLI client | Client Layer | J02 or explicitly bounded in-process C03 | operator workflow | RESOLVED | MISSING | Thin client; no quantitative/storage logic |
| J05 | TUI client | Client Layer | J02 | interactive workflow | RESOLVED | MISSING | Same canonical semantics |
| J06 | App UI | Client Layer | J02 | product workflow | RESOLVED | MISSING | Business logic remains behind service boundary |
| J07 | Paper/shadow mode | Runtime | A11,H05,I05,J02,K03 | J08 | RESOLVED | MISSING | Same decisions, simulated routing, explicit evidence |
| J08 | Live product mode | Runtime/Operations | J07,K02,K03,K05,K06,K08,K09,K10 | production | RESOLVED | MISSING | Explicit authorization/audit/recovery gates |

### K — Operations

| ID | Capability | Owner | Requires | Unlocks | Decision | Impl | Acceptance / authority |
|---|---|---|---|---|---|---|---|
| K01 | Provisioning/fixtures/CI | Engineering | — | all spines | RESOLVED | COMPLETE | Reproducible validation environment |
| K02 | Server access/runtime identity | Operations | C05,K01 | J08 | OPEN_BLOCKING | MISSING | Least privilege + explicit credential disposition; DG-H identity branch |
| K03 | Observability | Operations | K01 | J07,J08,K10 | RESOLVED | COMPLETE | Minimum externally observable health/provenance/failure transitions |
| K04 | Capacity observation | Operations | A05,K01 | K05 | RESOLVED | COMPLETE | Report exact usage/free space without control action |
| K05 | Health/pressure policy | Operations | K04 | A11,K06,K07 | FROZEN | COMPLETE | Explicit threshold/time-to-full action; ADR-0028 |
| K06 | RAW/source protection | Operations/Data Plane | A07,K05 | A11,K07,K08 | FROZEN | COMPLETE | Protection-identity/assessment seam, attributable-evidence; ADR-0032 |
| K07 | Tier relocation | Operations/Data Plane | K05,K06 | storage lifecycle | OPEN_BLOCKING | MISSING | Crash yields old or new valid placement; DG-H relocation branch |
| K08 | Backup/restore proof | Operations | K06 | K09,J08,K10,A11 | OPEN_BLOCKING | MISSING | Independent restore reproduces required identities; DG-H backup branch |
| K09 | Retention/deletion authority | Operations | K08 | sustainable live | OPEN_BLOCKING | MISSING | Never delete protected/sole recoverable evidence; DG-H deletion branch |
| K10 | Checkpoint/recovery | Operations/Data Plane | A11,K03,K08 | J08 | OPEN_BLOCKING | MISSING | Crash/restart preserves cursor/publication invariants; DG-H recovery branch; follows live acquisition rather than blocking its implementation |
| K11 | Governance-state consistency | Governance | K01 | reliable planning | RESOLVED | COMPLETE | Canonical docs represent accepted state without ambiguity; post-C03 reconciliation maintains coherence |

## Dependency integrity

The `Requires` relation is a DAG. In particular, operational capability and implementation sequencing are not conflated:

```text
A11 live acquisition -> K10 checkpoint/recovery implementation
K06 source protection -> K07 relocation
K06 source protection -> K08 backup/restore proof -> K09 deletion authority
```

A11 does not require implemented K10; recovery semantics must be resolved through the relevant DG-H branch before K10 implementation. K10 depends on the live acquisition capability it checkpoints. Backup/restore does not require tier-relocation implementation.

## Decision gates

The 23 unresolved classified decisions are grouped into **gate families** (15 `OPEN_BLOCKING`, 8 `OPEN_DEFERABLE`). A gate-family name is not a requirement to resolve every atom inside it. Selecting an atom activates only unresolved propositions on that atom's transitive dependency path. DG-D, the H01 branch of DG-A and DG-E are retained below as resolved references.

### DG-A — Representation / Feature integration

Remaining open atom-specific branch:

- `D05` candle-materialization identity: activate only when persisted Candle results are selected.

The canonical H01 branch is **RESOLVED** under ADR-0035. `D06` FootprintDefinition v1 is frozen and complete under ADR-0027; `E02` FeatureDefinition v1 is frozen and complete under ADR-0026; `E04` FeatureArtifact v1 is frozen and complete under ADR-0034; E05 is complete; E06 semantic integration is frozen under ADR-0035 and implementation is `COMPLETE` (issue #83/PR #88).

ADR-0035 fixes Application-owned cross-owner composition, two independent direct-Footprint FeatureDefinitions, exact parameters/input/output contracts, bucket-scoped FINAL observations including covered-empty output, D06 decimal -> E05 float calculation semantics, the exact H01 observation universe, exact D06 provenance binding, and one `h01_imbalance@1` FeatureSetDefinition.

`D05` is **not** a prerequisite of `E06`. DG-A does not block D03 or pure H01 kernel E05. E06 implementation is complete; no architecture decision gate remains on this branch.

### DG-B — Historical / Live data convergence — repair branch RESOLVED

Repair branch (`A16` quality lifecycle + `A10` repair/reconciliation + `B04` disjoint coverage) is frozen and complete for the accepted first vertical under ADR-0028/ADR-0029/ADR-0033, with two accepted known limitations tracked in ADR-0033 (coverage-trigger re-verification; partial candidate identity binding). General quality-report -> lifecycle mapping beyond the first vertical remains open, activated only by the repair slice actually needing it.

Live branch: `A11` live acquisition + `B06` live cursor, plus only the repair/shared semantics and DG-H prerequisites actually present on that path, remain the only open DG-B decisions.

Historical repair does not activate live-cursor semantics merely because both belong to DG-B.

### DG-C — Market-data depth

Atom-specific branches:

- L1 branch: `A13` when a concrete L1 feed is selected;
- L2 branch: `A14` only when L2 is selected, after A13 as declared by the DAG.

L1 does not activate L2. L3/MBO remains deferable until real L3 evidence exists.

### DG-D — Application configuration — RESOLVED

Atom: `C05`.

The C05 semantic/ownership decision is frozen:

```text
CLI / environment
      ↓
executable boundary: acquire + resolve input only
      ↓
typed immutable capability-specific Application config
      ↓
quant_platform.application: concrete composition owner
```

Resolution precedence is **explicit CLI > environment > declared default > explicit failure**. Application receives resolved values only, owns concrete composition, and does not read process arguments/environment directly. No generic DI container, service locator, provider registry or plugin/config framework is introduced.

C05 implementation is `COMPLETE`. C04/ASS-03 is also `COMPLETE` (issue #40/PR #44, 2026-09-14) -- this was missed in the 2026-09-19 post-Wave-1 reconciliation pass and corrected here after a fresh issue (#84) was drafted against the stale `MISSING` classification and found to be fully redundant against the current baseline.

### DG-E — Validation semantics — RESOLVED

Both DG-E branches are semantically frozen:

- **F07 validation/label path:** ADR-0036 / issue #95; implementation `MISSING`.
- **F08 robust-comparison path:** ADR-0037 / issue #97; DSR-L, complete comparable excess-return panel, explicit `K_eff`, binary64 numerical policy, full CSCV PBO and pinned vectors; implementation `MISSING`.

DG-E therefore has no remaining `OPEN_BLOCKING` decision. F08 remains independent from Strategy/Supervised paths that depend on implemented F07 rather than F08.

### DG-F — Strategy / Execution semantics

Atoms: `G04,H03`.

Trigger only when their dependent strategy/replay path is selected. Freeze session/cooldown and same-bar/OCO/partial-fill behavior with adversarial vectors.

### DG-G — Experiment / RL / Jobs

`I02` experiment persistence is resolved and complete (one restart-safe canonical persistence model). Remaining atoms: `I06,I07,J03`.

Independent branches: Strategic RL and Execution RL only before their own runtimes; Job semantics before durable long-running operations.

### DG-H — Operational safety

`K03` observability and `K05`/`K06` pressure/source-protection are resolved and complete (ADR-0028, ADR-0032). Remaining atoms: `K02,K07,K08,K09,K10`.

Progressive independent branches. Important order relationships are:

```text
K04 -> K05
K06 -> K07
K06 -> K08 -> K09
A11 + K03 + K08 -> K10
```

Only operational prerequisites of the selected atom are activated.

## Vertical milestones

| ID | Path | Proposition | State |
|---|---|---|---|
| V1 | Source -> canonical -> catalog -> DataGateway | First published data vertical is deterministically readable through canonical access | COMPLETE |
| V2 | DataGateway -> Application | Semantic historical request reaches canonical bounded data without caller storage identity | COMPLETE (`C02 + C03`) |
| V3 | DataGateway -> historical Candle | Bounded trades yield reproducible CLOSED candles | COMPLETE (`D03`) |
| V4 | Representation -> Feature -> H01 | Canonical footprint + FeatureDefinition/Artifact produce H01 with provenance | COMPLETE (`E06`; ADR-0035, issue #83/PR #88) |
| V5 | Feature -> Research | Feature artifacts produce reproducible Event/Outcome studies and sweeps | COMPLETE (`F02`/`F03`/`F04`; issues #85/#86/#92, PR #89/#90/#94) |
| V6 | Research -> Validation | Populations are leakage-safe and labels/censoring are explicit | SEMANTICS FROZEN (`F06` ADR-0031 + `F07` ADR-0036); F07 implementation MISSING |
| V7 | Strategy -> Replay | Decision/risk becomes deterministic orders/fills/portfolio replay | BLOCKED by DG-F |
| V8 | Historical -> Live | Historical and live paths converge under repair/coverage/cursor/recovery/storage guarantees | BLOCKED by DG-B live branch + relevant DG-H branches; acyclic |
| V9 | Application -> API -> Client | One application semantic implementation serves thin clients | APPLICATION SERVICE COMPLETE; transport deferred |
| V10 | Paper -> Live | Full canonical stack crosses explicit operational authorization gate | BLOCKED |

## Execution frontier

F07 and F08 semantics are now both `FROZEN`; their declared prerequisites are complete and both implementations are `MISSING`.

```text
F07 implementation   READY BY DEPENDENCY/DECISION STATE; issue #96 prepared; NOT AUTHORIZED by frontier alone
F08 implementation   READY BY DEPENDENCY/DECISION STATE; separate bounded issue required; NOT AUTHORIZED by frontier alone
```

F07 remains the required path to G01/I04. F08 is an independent robustness capability and does not block those downstream atoms.

This is a **frontier, not authorization**. `SCOPE.md` must activate exactly one bounded implementation or decision slice before runtime mutation.

## Execution waves

The waves below are dependency/value groupings, not a new linear phase numbering, and are a different concept from the `implement/wave-1` git branch/orchestrator batch (issues #51-#77): that branch's work cut across several of these DAG waves at once rather than completing exactly one. The `implement/wave-1` git batch is concluded as of 2026-09-19.

```text
Wave 0  Architecture foundation                           COMPLETE
Wave 1  First canonical computation/application slices    COMPLETE (C05,D03,E05,F05,I01,K04)
Wave 2  Representation / Feature vertical                 COMPLETE (E04,E06; ADR-0034/ADR-0035)
Wave 3  Research / Validation                             F01-F06 COMPLETE; F07/F08 FROZEN/MISSING and implementation-ready by state
Wave 4  Strategy / Replay                                 G01-G03 then DG-F/H01-H05
Wave 5  Experiment / Supervised ML                       I01,I02 COMPLETE; I03-I05 remain
Wave 6  Live Data Plane                                   K03,K04,K05,K06,A10,A16 COMPLETE; K02,K07-K10,A11,B06 remain
Wave 7  Runtime / Clients                                 J02 -> J04/J05/J06 when real client need exists
Wave 8  Paper / Live product                             J07 then J08 after required data/execution/ops gates
```

Parallelism is allowed whenever DAG dependencies are satisfied. Wave 3 is semantically frozen through F08 but remains implementation-incomplete until F07 and F08 runtimes are completed.

## Macro roadmap relationship

The numbered phases 0–15 remain the product-progression view. They are not executable work packets.

```text
Phase          = product progression
Atom           = executable capability proposition
Decision Gate  = atom-specific semantic/architecture authorization
Vertical       = cross-spine integration proof
Wave           = planning grouping of currently coherent work
```

Do not renumber phases merely to express capability dependencies.

## Deferable decisions

These stay open until their evidence trigger exists:

```text
A12  second-venue trades / general capability mechanism
A15  exact L3/MBO contract
B05  public DatasetSnapshot shape
B08  schema evolution beyond accepted versions
C06  multi-venue/multi-representation resolver mechanism
E07  provider-extension mechanism
H06  multi-asset execution
J02  concrete API transport
```

They are classified, not unknown, and therefore count as roadmap-defined without being semantically frozen.

## Governance rule

Before authorizing any atom:

1. verify current `origin/main`;
2. locate the atom here;
3. verify every `Requires` edge on its transitive path;
4. activate only the unresolved decision branches on that path;
5. credit existing evidence;
6. define the exact missing proposition and minimum proof;
7. open a bounded `SCOPE.md`/branch;
8. do not implement unrelated frontier atoms.

Accepted semantic authority always overrides this dependency view if a conflict is found.
