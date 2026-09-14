# Quant Platform Capability DAG vNext

Status: **POST-WAVE-1 TARGET STATE; governance merge requires actual E05 integration in `main`**.

This document is the execution/dependency view of the Quant Platform roadmap. `ROADMAP.md` remains the human-readable macro progression; `CAPABILITY_MAP.md` remains the compact current-state view; accepted ADRs/contracts remain semantic authority.

The operator has explicitly instructed sequencing to treat PR #50 / E05 as closed. At preparation time GitHub still reported PR #50 open, so this governance branch is a target-state reconciliation and MUST NOT merge into `main` until authoritative `main` contains the accepted E05 implementation.

## Planning model

Roadmap readiness is not the same thing as freezing every future semantic choice.

An atom is roadmap-defined when its observable outcome, owner, dependencies, unlocks, acceptance proposition and decision state are classified, and any unresolved decision is either assigned to an atom-specific blocking gate or has an explicit deferable evidence trigger.

Current audited inventory after post-Wave-1 closeout:

```text
TOTAL_ATOMS                     = 87
CLASSIFIED_ATOMS                = 87
UNCLASSIFIED_GAPS               = 0
SEMANTIC_FROZEN_OR_RESOLVED     = 50 / 87 = 57.5%
OPEN_BLOCKING                   = 29
OPEN_DEFERABLE                  = 8
ROADMAP_DEFINED                 = 50 + 29 + 8 = 87
ROADMAP_PLANNING_COMPLETENESS   = 87 / 87 = 100%
```

The 100% planning score does **not** mean 100% of future semantics are frozen. It means every atom is classified and every unresolved proposition has a bounded activation rule. The 57.5% semantic-freeze/resolution score must not be increased by prematurely deciding second-provider, live, RL, transport or operational semantics.

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
| A10 | Backfill and repair | Data Plane | A09,A16 | A11 | OPEN_BLOCKING | MISSING | Reconciliation/retry semantics; DG-B repair branch |
| A11 | Live trades acquisition | Data Plane | A10,K05,K06,K08 | B06,K10,J07 | OPEN_BLOCKING | MISSING | Restart/overlap/duplicate invariants; DG-B live branch + relevant DG-H |
| A12 | Second-venue trades | Data Plane | A11,real provider evidence | C06 | OPEN_DEFERABLE | MISSING | Venue ordering/eligibility profile; evidence-triggered |
| A13 | L1 contract/acquisition | Data Plane | A01,A04,real feed | A14 | OPEN_BLOCKING | MISSING | Versioned schema/ordering/provenance; DG-C L1 branch |
| A14 | L2 contract/acquisition | Data Plane | A13,real feed | A15 | OPEN_BLOCKING | MISSING | Snapshot/increment/gap semantics; DG-C L2 branch |
| A15 | L3/MBO contract | Data Plane | real L3 feed | advanced research | OPEN_DEFERABLE | MISSING | Map real feed without semantic invention |
| A16 | General quality lifecycle | Data Plane | A04,A08 | A10,A11 | OPEN_BLOCKING | PARTIAL | SELECTED decision backlog; general lifecycle beyond first vertical |

### B — Data Access

| ID | Capability | Owner | Requires | Unlocks | Decision | Impl | Acceptance / authority |
|---|---|---|---|---|---|---|---|
| B01 | Data Access boundary | Data Access | A05 | B02 | RESOLVED | COMPLETE | Canonical `quant_platform.access`; ADR-0019/24 |
| B02 | Bounded historical scan | Data Access | A09,B01 | C02,D03 | FROZEN | COMPLETE | Ordered memory-bounded scan; DATA_GATEWAY/ADR-0023 |
| B03 | Result identity/provenance | Data Access | B02 | B05,F04,B06 | FROZEN | COMPLETE | Semantic/source change changes identity; DATA_GATEWAY |
| B04 | Non-contiguous coverage read | Data Access | A04,A09 | A10,A11 | OPEN_BLOCKING | MISSING | SELECTED decision backlog; explicit disjoint coverage; DG-B repair branch |
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
| C04 | Tool orchestration convergence | Application | C02,C03,C05 | governed entry points | RESOLVED | COMPLETE | PR #44; exact ASS-03 debt reached zero through the canonical Application seam |
| C05 | Configuration convergence | Engineering/Application | C01 | C04,K02 | RESOLVED | COMPLETE | PR #43; typed immutable capability-specific config; Application owns composition |
| C06 | Multi-capability resolution | Application | second venue/representation evidence | general application service | OPEN_DEFERABLE | MISSING | Unsupported combinations explicit; no speculative registry |

### D — Representation

| ID | Capability | Owner | Requires | Unlocks | Decision | Impl | Acceptance / authority |
|---|---|---|---|---|---|---|---|
| D01 | Representation identity | Representation | — | C02,D02,E02 | RESOLVED | PARTIAL | Separate from DatasetIdentity; Consumer/Core |
| D02 | CandleDefinition v1 | Representation | A01,D01 | D03,D04 | FROZEN | COMPLETE | Accepted definition/hash/schema semantics; ADR-0021 |
| D03 | Historical candle computation | Representation | B02,D02 | D05,E03 | FROZEN | COMPLETE | PR #47; reproducible CLOSED candles on demand |
| D04 | Incremental/live candles | Representation | B06,D02 | J07 | FROZEN | MISSING | PARTIAL converges to historical-equivalent CLOSED output |
| D05 | Candle materialization identity | Representation | D02,D03,A02,A03 | research reuse | OPEN_BLOCKING | MISSING | Persisted series binds definition/source/support; DG-A candle-materialization branch |
| D06 | Footprint representation | Representation | A01,D01 | E06 | OPEN_BLOCKING | MISSING | SELECTED decision backlog; grain/tick grid/adjacency/availability for canonical H01 |

### E — Feature

| ID | Capability | Owner | Requires | Unlocks | Decision | Impl | Acceptance / authority |
|---|---|---|---|---|---|---|---|
| E01 | Definition/set/artifact separation | Feature Engine | D01 | E02-E04 | FROZEN | COMPLETE | Distinct lifecycle identities; ADR-0016 |
| E02 | FeatureDefinition v1 | Feature Engine | E01,D01 | E03,E04,E06 | OPEN_BLOCKING | MISSING | SELECTED decision backlog; versioned semantics + availability + identity |
| E03 | FeatureSet catalog/provider | Feature Engine | E01,E02 | E04 | RESOLVED | PARTIAL | Ordered provider resolution; ADR-0016/CM |
| E04 | FeatureArtifact/materialization | Feature Engine | E02,E03,A02 | F01,I04,E06 | OPEN_BLOCKING | MISSING | Artifact binds definitions/source/implementation; DG-A feature-artifact branch |
| E05 | H01 pure imbalance kernel | Feature Engine | Legacy Harvest audit | E06 | RESOLVED | COMPLETE | Accepted PR #50 pure ADOPT kernel; does not imply canonical H01 integration |
| E06 | H01 canonical integration | Feature Engine | D06,E02,E04,E05 | F02 | OPEN_BLOCKING | MISSING | Canonical footprint + provenance; DG-A canonical-H01 branch; D05 is not a prerequisite |
| E07 | Custom/provider extension | Feature Engine | E02,real second-provider need | broader library | OPEN_DEFERABLE | MISSING | Let second provider drive minimum extension seam |

### F — Research & Evaluation

| ID | Capability | Owner | Requires | Unlocks | Decision | Impl | Acceptance / authority |
|---|---|---|---|---|---|---|---|
| F01 | HypothesisSpec | Research | E02 | F02,F03 | RESOLVED | MISSING | Reproducible hypothesis references canonical observables |
| F02 | EventSpec/detection | Research | F01,E04 | F04 | RESOLVED | MISSING | Traceable event rule + availability evidence |
| F03 | OutcomeSpec/Outcome | Research | F01 | F04,F07 | RESOLVED | MISSING | Horizon/censoring/availability explicit |
| F04 | Event study/sweeps | Research | F02,F03,B03 | I01,F08 | RESOLVED | MISSING | Re-run preserves population and aggregates |
| F05 | Walk-forward schedule | Validation | shared time primitives | F06,F08 | RESOLVED | COMPLETE | PR #48; deterministic expanding half-open temporal folds |
| F06 | Availability/purge/embargo | Validation | F05,E02 | F07,I04 | OPEN_BLOCKING | MISSING | Adversarial leakage rejected; DG-E validation branch |
| F07 | Labels/censoring/lockbox | Validation | F03,F06 | G01,I04 | OPEN_BLOCKING | MISSING | Explicit boundary/censoring/hidden evaluation; DG-E validation branch |
| F08 | DSR/PBO | Research/Validation | F04,F05 | robust comparison | OPEN_BLOCKING | MISSING | Pinned estimator/input/numeric vectors; DG-E DSR/PBO branch only |

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
| I01 | Study/Trial/Run/Artifact semantic model | Experiment System | Core concepts | I02,I03 | RESOLVED | COMPLETE | PR #49; canonical deterministic experiment identity family |
| I02 | Experiment persistence | Experiment System | I01,A05 | I03,J03 | OPEN_BLOCKING | MISSING | SELECTED decision backlog; one restart-safe canonical persistence model |
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
| K03 | Observability | Operations | K01 | J07,J08,K10 | OPEN_BLOCKING | MISSING | SELECTED decision backlog; minimum externally observable health/provenance/failure contract |
| K04 | Capacity observation | Operations | A05,K01 | K05 | RESOLVED | COMPLETE | PR #46; exact observation-only usage/free-space reporting |
| K05 | Health/pressure policy | Operations | K04 | A11,K06,K07 | OPEN_BLOCKING | MISSING | SELECTED decision backlog; explicit threshold/time-to-full action; DG-H pressure branch |
| K06 | RAW/source protection | Operations/Data Plane | A07,K05 | A11,K07,K08 | OPEN_BLOCKING | MISSING | Protected evidence reconstructs canonical data; DG-H protection branch |
| K07 | Tier relocation | Operations/Data Plane | K05,K06 | storage lifecycle | OPEN_BLOCKING | MISSING | Crash yields old or new valid placement; DG-H relocation branch |
| K08 | Backup/restore proof | Operations | K06 | K09,J08,K10,A11 | OPEN_BLOCKING | MISSING | Independent restore reproduces required identities; DG-H backup branch |
| K09 | Retention/deletion authority | Operations | K08 | sustainable live | OPEN_BLOCKING | MISSING | Never delete protected/sole recoverable evidence; DG-H deletion branch |
| K10 | Checkpoint/recovery | Operations/Data Plane | A11,K03,K08 | J08 | OPEN_BLOCKING | MISSING | Crash/restart preserves cursor/publication invariants; DG-H recovery branch; follows live acquisition rather than blocking its implementation |
| K11 | Governance-state consistency | Governance | K01 | reliable planning | RESOLVED | COMPLETE | Canonical target state coherent through post-Wave-1 closeout |

## Dependency integrity

The `Requires` relation is a DAG. In particular, operational capability and implementation sequencing are not conflated:

```text
A11 live acquisition -> K10 checkpoint/recovery implementation
K06 source protection -> K07 relocation
K06 source protection -> K08 backup/restore proof -> K09 deletion authority
```

A11 does not require implemented K10; recovery semantics must be resolved through the relevant DG-H branch before K10 implementation. K10 depends on the live acquisition capability it checkpoints. Backup/restore does not require tier-relocation implementation.

## Decision gates

The 29 open blockers are grouped into **gate families**. A gate-family name is not a requirement to resolve every atom inside it. Selecting an atom activates only unresolved propositions on that atom's transitive dependency path. DG-D is retained below as a resolved/completed historical reference.

### DG-A — Representation / Feature integration

Atom-specific branches:

- `D05` candle-materialization identity: activate only when persisted Candle results are selected;
- `E02` FeatureDefinition: SELECTED for next decision resolution;
- `E04` FeatureArtifact: activate after E02/E03 when feature artifact/materialization is selected;
- `D06`: SELECTED for next decision resolution on the canonical H01 path;
- `E05`: pure H01 kernel COMPLETE;
- `E06`: canonical H01 integration remains blocked until D06 + E02 + E04 are satisfied.

`D05` is **not** a prerequisite of `E06`. Do not create a generic provider/plugin framework.

### DG-B — Historical / Live data convergence

Atom-specific branches:

- repair branch: `A16` quality lifecycle + `B04` disjoint coverage are SELECTED next decisions; `A10` follows only after the required repair propositions are frozen;
- live branch: `A11` live acquisition + `B06` live cursor, plus only the repair/shared semantics and DG-H prerequisites actually present on that path.

Historical repair does not activate live-cursor semantics merely because both belong to DG-B.

### DG-C — Market-data depth

Atom-specific branches:

- L1 branch: `A13` when a concrete L1 feed is selected;
- L2 branch: `A14` only when L2 is selected, after A13 as declared by the DAG.

L1 does not activate L2. L3/MBO remains deferable until real L3 evidence exists.

### DG-D — Application configuration — RESOLVED / COMPLETE

Atoms: `C05,C04`.

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

PR #43 completed C05 implementation. PR #44 then completed C04/ASS-03 tool convergence through that seam. DG-D has no remaining live blocker at this target state.

### DG-E — Validation semantics

Atom-specific branches:

- validation/label path: `F06` availability/purge/embargo then `F07` labels/censoring/lockbox;
- robust-comparison path: `F08` DSR/PBO, activated only when that estimator capability is selected.

F06 remains downstream of E02, so it is not selected ahead of E02. F08 does not block Strategy or supervised input, whose declared dependency path runs through F07 rather than F08.

### DG-F — Strategy / Execution semantics

Atoms: `G04,H03`.

Trigger only when their dependent strategy/replay path is selected. Freeze session/cooldown and same-bar/OCO/partial-fill behavior with adversarial vectors.

### DG-G — Experiment / RL / Jobs

Atoms: `I02,I06,I07,J03`.

`I02` experiment persistence is SELECTED for next decision resolution. Strategic RL and Execution RL remain deferred to their own selected runtimes; Job semantics follows the experiment-persistence decision when durable long-running operations are selected.

### DG-H — Operational safety

Atoms: `K02,K03,K05,K06,K07,K08,K09,K10`.

`K03` observability and `K05` pressure policy are SELECTED for next decision resolution. Important order relationships remain:

```text
K04 -> K05
K06 -> K07
K06 -> K08 -> K09
A11 + K03 + K08 -> K10
```

Only operational prerequisites of the selected atom are activated. K06 remains downstream of K05.

## Vertical milestones

| ID | Path | Proposition | State |
|---|---|---|---|
| V1 | Source -> canonical -> catalog -> DataGateway | First published data vertical is deterministically readable through canonical access | COMPLETE |
| V2 | DataGateway -> Application | Semantic historical request reaches canonical bounded data without caller storage identity | COMPLETE (`C02 + C03`) |
| V3 | DataGateway -> historical Candle | Bounded trades yield reproducible CLOSED candles | COMPLETE (`D03`) |
| V4 | Representation -> Feature -> H01 | Canonical footprint + FeatureDefinition/Artifact produce H01 with provenance | BLOCKED by D06 + E02 + E04 + E06; E05 kernel COMPLETE |
| V5 | Feature -> Research | Feature artifacts produce reproducible Event/Outcome studies | BLOCKED by E04 |
| V6 | Research -> Validation | Populations are leakage-safe and labels/censoring are explicit | BLOCKED by DG-E validation branch |
| V7 | Strategy -> Replay | Decision/risk becomes deterministic orders/fills/portfolio replay | BLOCKED by DG-F |
| V8 | Historical -> Live | Historical and live paths converge under repair/coverage/cursor/recovery/storage guarantees | BLOCKED by DG-B live branch + relevant DG-H branches; acyclic |
| V9 | Application -> API -> Client | One application semantic implementation serves thin clients | APPLICATION + EXECUTABLE CONVERGENCE COMPLETE; transport deferred |
| V10 | Paper -> Live | Full canonical stack crosses explicit operational authorization gate | BLOCKED |

## Implementation frontier

The original ready-slice implementation frontier is exhausted:

```text
(empty)
```

No implementation should be invented merely to occupy an agent. New implementation supply requires bounded decision resolution first.

## Selected next decision backlog

```text
E02  FeatureDefinition v1 semantics                 DG-A
D06  Footprint representation semantics             DG-A
K05  Health / pressure policy                       DG-H
A16  General quality lifecycle                      DG-B
B04  Non-contiguous coverage read                   DG-B
I02  Experiment persistence model                   DG-G
K03  Minimum observability contract                 DG-H
```

All seven remain `OPEN_BLOCKING`; selection is not resolution or implementation authorization.

## Execution waves

The waves are dependency/value groupings, not a new linear phase numbering.

```text
Wave 0  Architecture foundation                           COMPLETE
Wave 1  First canonical computation/application slices    COMPLETE
Wave 2  Representation / Feature vertical                 E02 + D06 decisions, then E04/E06
Wave 3  Research / Validation                             F01-F04 then DG-E validation branch
Wave 4  Strategy / Replay                                 G01-G03 then DG-F/H01-H05
Wave 5  Experiment / Supervised ML                       I01 complete; I02 decision next, then I03-I05
Wave 6  Live Data Plane                                   K04 complete; K05/A16/B04 next, then protection/repair/live
Wave 7  Runtime / Clients                                 J02 -> J04/J05/J06 when real client need exists
Wave 8  Paper / Live product                             J07 then J08 after required data/execution/ops gates
```

Parallelism is allowed whenever DAG dependencies are satisfied. The selected next decision backlog deliberately spans independent paths so implementation supply can be replenished progressively as each decision freezes.

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
7. open a bounded `SCOPE.md`/issue/branch;
8. do not implement unrelated atoms.

Accepted semantic authority always overrides this dependency view if a conflict is found.