# Quant Platform Capability DAG vNext

Status: **PROPOSED on governance branch; canonical only after merge**.

This document is the execution/dependency view of the Quant Platform roadmap. `ROADMAP.md` remains the human-readable macro progression; `CAPABILITY_MAP.md` remains the compact current-state view; accepted ADRs/contracts remain semantic authority.

## Planning model

Roadmap readiness is not the same thing as freezing every future semantic choice.

An atom is planning-complete when its observable outcome, owner, dependencies, unlocks, acceptance proposition and decision state are classified. A future choice may remain `OPEN_DEFERABLE` when authority already defines the trigger/evidence required to resolve it later.

Current audited inventory:

```text
TOTAL_ATOMS                     = 87
CLASSIFIED_ATOMS                = 87
UNCLASSIFIED_GAPS               = 0
SEMANTIC_FROZEN_OR_RESOLVED     = 49 / 87 = 56.3%
OPEN_BLOCKING                   = 30
OPEN_DEFERABLE                  = 8
ROADMAP_PLANNING_COMPLETENESS   = >= 90%
```

`56.3%` measures semantics already frozen/resolved across the full future platform. It is **not** the roadmap-planning completeness score and must not be increased by prematurely freezing future-provider, live, RL, transport or operational choices.

Decision states:

- `FROZEN` — contract/semantic authority is accepted and versioned.
- `RESOLVED` — planning/ownership/architecture proposition is decided; implementation may still be missing.
- `OPEN_BLOCKING` — must be resolved before the dependent atom is authorized.
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
| A07 | Bybit historical acquisition | Source/Producer | A01 | A08 | FROZEN | PARTIAL | Reference vertical proven; Ingest/ADR-0023 |
| A08 | S13 certification | Producer | A03,A04,A06,A07 | A09 | FROZEN | COMPLETE | Durable fail-closed evidence; ADR-0023 |
| A09 | S14 publication | Producer | A05,A08 | B02 | FROZEN | COMPLETE | Eligible partition becomes readable; ADR-0023 |
| A10 | Backfill and repair | Data Plane | A09,A16 | A11 | OPEN_BLOCKING | MISSING | Reconciliation/retry semantics; DG-B |
| A11 | Live trades acquisition | Data Plane | A10,K05,K06,K08,K10 | B06,J07 | OPEN_BLOCKING | MISSING | Restart/overlap/duplicate invariants; DG-B |
| A12 | Second-venue trades | Data Plane | A11,real provider evidence | C06 | OPEN_DEFERABLE | MISSING | Venue ordering/eligibility profile; evidence-triggered |
| A13 | L1 contract/acquisition | Data Plane | A01,A04,real feed | A14 | OPEN_BLOCKING | MISSING | Versioned schema/ordering/provenance; DG-C |
| A14 | L2 contract/acquisition | Data Plane | A13,real feed | A15 | OPEN_BLOCKING | MISSING | Snapshot/increment/gap semantics; DG-C |
| A15 | L3/MBO contract | Data Plane | real L3 feed | advanced research | OPEN_DEFERABLE | MISSING | Map real feed without semantic invention |
| A16 | General quality lifecycle | Data Plane | A04,A08 | A10,A11 | OPEN_BLOCKING | PARTIAL | Deterministic lifecycle beyond first vertical; DG-B |

### B — Data Access

| ID | Capability | Owner | Requires | Unlocks | Decision | Impl | Acceptance / authority |
|---|---|---|---|---|---|---|---|
| B01 | Data Access boundary | Data Access | A05 | B02 | RESOLVED | COMPLETE | Canonical `quant_platform.access`; ADR-0019/24 |
| B02 | Bounded historical scan | Data Access | A09,B01 | C02,D03 | FROZEN | COMPLETE | Ordered memory-bounded scan; DATA_GATEWAY/ADR-0023 |
| B03 | Result identity/provenance | Data Access | B02 | B05,F04 | FROZEN | COMPLETE | Semantic/source change changes identity; DATA_GATEWAY |
| B04 | Non-contiguous coverage read | Data Access | A04,A09 | A10,A11 | OPEN_BLOCKING | MISSING | Explicit disjoint coverage; DG-B |
| B05 | Durable DatasetSnapshot | Data Access | B03 | experiment replay | OPEN_DEFERABLE | MISSING | Immutable reproducible reference; trigger on durable replay need |
| B06 | Live access/cursor | Data Access | A11,B03 | D04,J07 | OPEN_BLOCKING | MISSING | Deterministic resume/replay; DG-B |
| B07 | In-process batch surface | Data Access | B02 | C02,D03 | RESOLVED | COMPLETE | Existing bounded `DataScan`; concrete naming local |
| B08 | Schema evolution policy | Data Access | new schema evidence | A13-A15 | OPEN_DEFERABLE | MISSING | Resolve when a new real schema/version appears |

### C — Application

| ID | Capability | Owner | Requires | Unlocks | Decision | Impl | Acceptance / authority |
|---|---|---|---|---|---|---|---|
| C01 | Application ownership | Application | B01 | C02-C05 | RESOLVED | COMPLETE | `quant_platform.application`; ASS-01/ADR-0024 |
| C02 | ASS-02 semantic selector resolution | Application | C01,B02,D01,J01 | C03 | FROZEN | MISSING | Semantic request resolves without storage identity; Consumer API |
| C03 | ASS-02 result/error translation | Application | C02 | C04,J02,J04-J06 | FROZEN | MISSING | Stable envelope/error semantics; Consumer API |
| C04 | Tool orchestration convergence | Application | C02,C03,C05 | governed entry points | RESOLVED | MISSING | Exact ASS-03 debt reaches zero |
| C05 | Configuration convergence | Engineering/Application | C01 | C04,K02 | OPEN_BLOCKING | MISSING | One explicit resolution convention; DG-D |
| C06 | Multi-capability resolution | Application | second venue/representation evidence | general application service | OPEN_DEFERABLE | MISSING | Unsupported combinations explicit; no speculative registry |

### D — Representation

| ID | Capability | Owner | Requires | Unlocks | Decision | Impl | Acceptance / authority |
|---|---|---|---|---|---|---|---|
| D01 | Representation identity | Representation | — | C02,D02,E02 | RESOLVED | PARTIAL | Separate from DatasetIdentity; Consumer/Core |
| D02 | CandleDefinition v1 | Representation | A01,D01 | D03,D04 | FROZEN | COMPLETE | Accepted definition/hash/schema semantics; ADR-0021 |
| D03 | Historical candle computation | Representation | B02,D02 | D05,E03 | FROZEN | MISSING | Reproducible CLOSED candles on demand |
| D04 | Incremental/live candles | Representation | B06,D02 | J07 | FROZEN | MISSING | PARTIAL converges to historical-equivalent CLOSED output |
| D05 | Candle materialization identity | Representation | D02,D03,A02,A03 | research reuse | OPEN_BLOCKING | MISSING | Persisted series binds definition/source/support; DG-A |
| D06 | Footprint representation | Representation | A01,D01 | E06 | OPEN_BLOCKING | MISSING | Explicit grain/tick grid/adjacency/availability; DG-A |

### E — Feature

| ID | Capability | Owner | Requires | Unlocks | Decision | Impl | Acceptance / authority |
|---|---|---|---|---|---|---|---|
| E01 | Definition/set/artifact separation | Feature Engine | D01 | E02-E04 | FROZEN | COMPLETE | Distinct lifecycle identities; ADR-0016 |
| E02 | FeatureDefinition v1 | Feature Engine | E01,D01 | E03,E04,E06 | OPEN_BLOCKING | MISSING | Versioned semantics + availability + identity; DG-A |
| E03 | FeatureSet catalog/provider | Feature Engine | E01,E02 | E04 | RESOLVED | PARTIAL | Ordered provider resolution; ADR-0016/CM |
| E04 | FeatureArtifact/materialization | Feature Engine | E02,E03,A02 | F01,I04 | OPEN_BLOCKING | MISSING | Artifact binds definitions/source/implementation; DG-A |
| E05 | H01 pure imbalance kernel | Feature Engine | Legacy Harvest audit | E06 | RESOLVED | MISSING | Narrow ADOPT numeric/edge semantics |
| E06 | H01 canonical integration | Feature Engine | D06,E02,E04,E05 | F02 | OPEN_BLOCKING | MISSING | Canonical footprint + provenance; DG-A |
| E07 | Custom/provider extension | Feature Engine | E02,real second-provider need | broader library | OPEN_DEFERABLE | MISSING | Let second provider drive minimum extension seam |

### F — Research & Evaluation

| ID | Capability | Owner | Requires | Unlocks | Decision | Impl | Acceptance / authority |
|---|---|---|---|---|---|---|---|
| F01 | HypothesisSpec | Research | E02 | F02,F03 | RESOLVED | MISSING | Reproducible hypothesis references canonical observables |
| F02 | EventSpec/detection | Research | F01,E04 | F04 | RESOLVED | MISSING | Traceable event rule + availability evidence |
| F03 | OutcomeSpec/Outcome | Research | F01 | F04,F07 | RESOLVED | MISSING | Horizon/censoring/availability explicit |
| F04 | Event study/sweeps | Research | F02,F03,B03 | I01 | RESOLVED | MISSING | Re-run preserves population and aggregates |
| F05 | Walk-forward schedule | Validation | shared time primitives | F06 | RESOLVED | MISSING | Deterministic temporal fold boundaries |
| F06 | Availability/purge/embargo | Validation | F05,E02 | F07,I04 | OPEN_BLOCKING | MISSING | Adversarial leakage rejected; DG-E |
| F07 | Labels/censoring/lockbox | Validation | F03,F06 | G01,I04 | OPEN_BLOCKING | MISSING | Explicit boundary/censoring/hidden evaluation; DG-E |
| F08 | DSR/PBO | Research/Validation | F04,F05 | robust comparison | OPEN_BLOCKING | MISSING | Pinned estimator/input/numeric vectors; DG-E |

### G — Strategy & Decision

| ID | Capability | Owner | Requires | Unlocks | Decision | Impl | Acceptance / authority |
|---|---|---|---|---|---|---|---|
| G01 | StrategySpec/DecisionIntent | Strategy | F07 | G02,H01 | RESOLVED | MISSING | Same inputs/config => same intent/provenance |
| G02 | Policy composition | Strategy | G01 | G03 | RESOLVED | MISSING | Deterministic ordering/conflicts |
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
| I01 | Study/Trial/Run/Artifact semantic model | Experiment System | Core concepts | I02,I03 | RESOLVED | MISSING | One canonical experiment identity family |
| I02 | Experiment persistence | Experiment System | I01,A05 | I03,J03 | OPEN_BLOCKING | MISSING | One restart-safe canonical persistence model; DG-G |
| I03 | Trial accounting/comparison | Experiment System | I01,I02,H05 | model selection | RESOLVED | MISSING | Resume idempotent; comparable population identity |
| I04 | Supervised input/selection | Learning | E04,F06,F07 | I05 | RESOLVED | MISSING | Durable split/provenance/anti-leakage evidence |
| I05 | Supervised training/evaluation | Learning | I03,I04 | J07 | RESOLVED | MISSING | Run binds code/data/splits/model/metrics |
| I06 | Strategic RL contract | Strategic RL | G01,F07,I01 | RL runtime | OPEN_BLOCKING | MISSING | State/action/reward excludes execution-control task; DG-G |
| I07 | Execution RL contract | Execution RL | H01-H05,I01 | RL runtime | OPEN_BLOCKING | MISSING | Structurally separate execution task; DG-G |

### J — Runtime & Interface

| ID | Capability | Owner | Requires | Unlocks | Decision | Impl | Acceptance / authority |
|---|---|---|---|---|---|---|---|
| J01 | Consumer API semantics | Application/API | D01 | C02,J02 | FROZEN | COMPLETE | Stable semantic selector/result/error boundary; ADR-0020 |
| J02 | Canonical API transport | API Runtime | C03,real client need | J04-J06 | OPEN_DEFERABLE | MISSING | Preserve Consumer API semantics; transport intentionally deferred |
| J03 | Job runtime | Runtime | C03,I02 | long operations | OPEN_BLOCKING | MISSING | Durable identity/retry/result semantics; DG-G |
| J04 | CLI client | Client Layer | J02 or explicitly bounded in-process C03 | operator workflow | RESOLVED | MISSING | Thin client; no quantitative/storage logic |
| J05 | TUI client | Client Layer | J02 | interactive workflow | RESOLVED | MISSING | Same canonical semantics |
| J06 | App UI | Client Layer | J02 | product workflow | RESOLVED | MISSING | Business logic remains behind service boundary |
| J07 | Paper/shadow mode | Runtime | A11,H05,I05,J02,K03 | J08 | RESOLVED | MISSING | Same decisions, simulated routing, explicit evidence |
| J08 | Live product mode | Runtime/Operations | J07,K02-K10 | production | RESOLVED | MISSING | Explicit authorization/audit/recovery gates |

### K — Operations

| ID | Capability | Owner | Requires | Unlocks | Decision | Impl | Acceptance / authority |
|---|---|---|---|---|---|---|---|
| K01 | Provisioning/fixtures/CI | Engineering | — | all spines | RESOLVED | COMPLETE | Reproducible validation environment |
| K02 | Server access/runtime identity | Operations | C05,K01 | J08 | OPEN_BLOCKING | MISSING | Least privilege + explicit credential disposition; DG-H |
| K03 | Observability | Operations | K01 | J07,J08,K10 | OPEN_BLOCKING | MISSING | Critical transitions externally observable; DG-H |
| K04 | Capacity observation | Operations | A05,K01 | K05 | RESOLVED | MISSING | Report exact usage/free space without control action |
| K05 | Health/pressure policy | Operations | K04 | A11,K07 | OPEN_BLOCKING | MISSING | Explicit threshold/time-to-full action; DG-H |
| K06 | RAW/source protection | Operations/Data Plane | A07,K05,K08 | A10,A11 | OPEN_BLOCKING | MISSING | Protected evidence reconstructs canonical data; DG-H |
| K07 | Tier relocation | Operations/Data Plane | K05,K06 | K08 | OPEN_BLOCKING | MISSING | Crash yields old or new valid placement; DG-H |
| K08 | Backup/restore proof | Operations | K06,K07 | K09,J08,K10 | OPEN_BLOCKING | MISSING | Independent restore reproduces required identities; DG-H |
| K09 | Retention/deletion authority | Operations | K08 | sustainable live | OPEN_BLOCKING | MISSING | Never delete protected/sole recoverable evidence; DG-H |
| K10 | Checkpoint/recovery | Operations/Data Plane | A11,K03,K08 | J08 | OPEN_BLOCKING | MISSING | Crash/restart preserves cursor/publication invariants; DG-H |
| K11 | Governance-state consistency | Governance | K01 | reliable planning | RESOLVED | MISSING | Canonical docs represent accepted state without ambiguity |

## Decision gates

The open blockers are grouped into **activation gates**, not a mandatory up-front design program.

### DG-A — Representation / Feature integration

Atoms: `D05,D06,E02,E04,E06`.

Trigger: before Feature runtime or canonical H01 integration. Does not block C02/C03, D03 or the pure H01 kernel E05.

### DG-B — Historical / Live data convergence

Atoms: `A10,A11,A16,B04,B06`.

Trigger: before live acquisition/backfill-repair convergence. Resolve coverage, quality lifecycle, overlap/precedence/duplicates and cursor semantics together.

### DG-C — Market-data depth

Atoms: `A13,A14`.

Trigger: a concrete L1/L2 source/feed selected for implementation. L3/MBO remains deferable until a real L3 feed exists.

### DG-D — Application configuration

Atom: `C05`.

Trigger: before ASS-03. Resolve one explicit convention only; do not create a DI/plugin framework for symmetry.

### DG-E — Validation semantics

Atoms: `F06,F07,F08`.

Trigger: before the Validation → Strategy/ML vertical. H14 DSR/PBO remains isolated to its own capability until needed.

### DG-F — Strategy / Execution semantics

Atoms: `G04,H03`.

Trigger: before deterministic replay is claimed end-to-end. Freeze session/cooldown and same-bar/OCO/partial-fill behavior with adversarial vectors.

### DG-G — Experiment / RL / Jobs

Atoms: `I02,I06,I07,J03`.

Trigger independently by branch: experiment persistence before experiment runtime; Strategic RL and Execution RL only before their own runtimes; Job semantics before durable long-running operations.

### DG-H — Operational safety

Atoms: `K02,K03,K05,K06,K07,K08,K09,K10`.

Trigger progressively. This is not one monolithic precondition. Important internal order includes:

```text
K04 -> K05
K06 -> K08 -> K09
K03 + K08 -> K10
```

Only the operational prerequisites of a selected execution/live atom are activated.

## Vertical milestones

| ID | Path | Proposition | State |
|---|---|---|---|
| V1 | Source -> canonical -> catalog -> DataGateway | First published data vertical is deterministically readable through canonical access | COMPLETE |
| V2 | DataGateway -> Application | Semantic historical request reaches canonical bounded data without caller storage identity | READY_CHAIN (`C02 -> C03`) |
| V3 | DataGateway -> historical Candle | Bounded trades yield reproducible CLOSED candles | READY (`D03`) |
| V4 | Representation -> Feature -> H01 | Canonical footprint + FeatureDefinition/Artifact produce H01 with provenance | BLOCKED by DG-A |
| V5 | Feature -> Research | Feature artifacts produce reproducible Event/Outcome studies | BLOCKED by E04 |
| V6 | Research -> Validation | Populations are leakage-safe and labels/censoring are explicit | BLOCKED by DG-E |
| V7 | Strategy -> Replay | Decision/risk becomes deterministic orders/fills/portfolio replay | BLOCKED by DG-F |
| V8 | Historical -> Live | Historical and live paths converge under coverage/cursor/recovery/storage guarantees | BLOCKED by DG-B/DG-H |
| V9 | Application -> API -> Client | One application semantic implementation serves thin clients | APPLICATION READY; transport deferred |
| V10 | Paper -> Live | Full canonical stack crosses explicit operational authorization gate | BLOCKED |

## Execution frontier

Decision-complete, missing atoms whose declared dependencies are currently satisfied:

```text
C02  ASS-02 semantic selector resolution
D03  historical Candle runtime (on-demand only)
E05  H01 pure imbalance kernel
F05  deterministic walk-forward schedule
I01  Study/Trial/Run/Artifact semantic model
K04  observational capacity monitoring
K11  governance-state consistency
```

This is a **frontier, not authorization**. `SCOPE.md` must activate exactly one bounded implementation or decision slice before mutation.

## Execution waves

The waves are dependency/value groupings, not a new linear phase numbering.

```text
Wave 0  Architecture foundation                           COMPLETE
Wave 1  First canonical computation verticals             frontier above
Wave 2  Representation / Feature vertical                 DG-A then D05/D06/E02/E04/E06
Wave 3  Research / Validation                             F01-F04 then DG-E/F06/F07
Wave 4  Strategy / Replay                                 G01-G03 then DG-F/H01-H05
Wave 5  Experiment / Supervised ML                       I01, DG-G/I02, I03-I05
Wave 6  Live Data Plane                                   K04 + relevant DG-H, then DG-B/A10/A11/B06
Wave 7  Runtime / Clients                                 C03 -> J02 -> J04/J05/J06 when real client need exists
Wave 8  Paper / Live product                             J07 then J08 after required data/execution/ops gates
```

Parallelism is allowed whenever DAG dependencies are satisfied. In particular, ASS-02, historical Candle, pure H01, walk-forward semantics, experiment identity and observational capacity can be scoped independently.

## Macro roadmap relationship

The numbered phases 0–15 remain the product-progression view. They are not executable work packets.

```text
Phase          = product progression
Atom           = executable capability proposition
Decision Gate  = semantic/architecture authorization
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

They are classified, not unknown, and therefore do not prevent roadmap readiness.

## Governance rule

Before authorizing any atom:

1. verify current `origin/main`;
2. locate the atom here;
3. verify all `Requires` are satisfied;
4. activate any blocking decision gate for that atom, and only that gate;
5. credit existing evidence;
6. define the exact missing proposition and minimum proof;
7. open a bounded `SCOPE.md`/branch;
8. do not implement unrelated frontier atoms.

Accepted semantic authority always overrides this dependency view if a conflict is found.
