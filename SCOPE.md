# Scope: Wave 5 - Experiment & Supervised ML v1

Status: **ACTIVE**

Scope kind: **implementation, legacy-harvest and validation scope for Wave 5**.

Target integration branch: **`implement/wave-5`**, branched from `main` after Wave 4 promotion.

---

## Prerequisite Integration Gate

Wave 5 may start only after the Wave 4 Strategy / Replay path is promoted to `main`.

Current gate evidence:

```text
Wave 4 promotion PR: #165
main promotion commit: fa3075f297af677a562d57d20cfcfa83e6eba897
Wave 4 tag: wave-4-strategy-replay-v1
Issue #146: Golden V7 deterministic replay proof credited
Issue #147: Wave 4 governance closeout closed after main promotion
```

The dependency basis is `CAPABILITY_DAG.md`: `I03` requires `I01,I02,H05`; `I04` requires `E04,F06,F07`; `I05` requires both `I03,I04`.

`I03` and `I04` are therefore parallel prerequisites of `I05`. Any sequencing below is operational batching, not a claim that `I04` semantically depends on `I03`.

---

## Objective

Build and validate the canonical **Experiment & Supervised ML** vertical for reproducible supervised-model evaluation over already-canonical feature, label, validation and replay foundations:

```text
Canonical Feature Artifacts (E04) + Labels / Lockbox (F07)
                    + Availability / Purge / Embargo (F06)
                                      |
                                      v
                  Supervised input and selection (I04)
                                      |
                                      v
              Trial accounting and comparison (I03)
                                      |
                                      v
          Supervised training and evaluation (I05)
                                      |
                                      v
       Run-bound metrics, model artifacts and prediction artifacts
                                      |
                                      v
     Future Strategy consumption through learner-agnostic DecisionIntent
```

The goal is to make supervised learning a deterministic, auditable and reproducible platform capability, not an ad hoc notebook/runtime path. Wave 5 must:

1. construct supervised datasets and selections without lookahead leakage;
2. reuse the canonical validation primitives for walk-forward, availability, purge and embargo;
3. account for comparable trials through the existing Study / Trial / Run / Artifact identity and persistence model;
4. train and evaluate deterministic supervised model baselines under explicit environment and artifact identity;
5. emit model, prediction and metric artifacts through the canonical Experiment System.

Wave 5 delivers atoms **`I04`**, **`I03`** and **`I05`**. It does not implement strategic RL (`I06`), execution RL (`I07`), job runtime (`J03`), paper/shadow trading (`J07`) or live execution (`J08`).

---

## Authority

Read before mutating code or scope-derived issue bodies:

- `AGENTS.md`
- `README.md`
- `docs/product/PRODUCT.md`
- `docs/product/CAPABILITY_MAP.md`
- `docs/product/CAPABILITY_DAG.md`
- `docs/product/ROADMAP.md`
- `docs/architecture/TARGET_ARCHITECTURE.md`
- `docs/contracts/CORE_CONTRACTS.md` (especially sections 11-13, 19-20 and 28-31)
- `docs/architecture/OPEN_DECISIONS.md` (`DG-G`)
- `docs/legacy/ADOPTION_LEDGER.md`
- `docs/decisions/ADR-0006-temporal-semantics.md`
- `docs/decisions/ADR-0009-policy-decision-intent.md`
- `docs/decisions/ADR-0010-supervised-and-rl.md`
- `docs/decisions/ADR-0014-experiment-provenance.md`
- `docs/decisions/ADR-0024-package-boundary-modular-monolith-v1.md`
- `docs/decisions/ADR-0031-availability-purge-embargo-v1.md`
- `docs/decisions/ADR-0034-feature-artifact-v1.md`
- `docs/decisions/ADR-0036-labels-censoring-lockbox-v1.md`
- `docs/decisions/ADR-0037-dsr-pbo-robust-comparison-v1.md`

Accepted ADRs and frozen contracts remain normative semantic authority. Legacy `ml_core` is read-only evidence and must never become a runtime dependency.

---

## Baseline and Credited State

Authoritative baseline for this branch:

```text
main @ fa3075f297af677a562d57d20cfcfa83e6eba897
tag  wave-4-strategy-replay-v1
```

Credit, do not reimplement or re-prove absent invalidating evidence:

```text
I01 Study / Trial / Run / Artifact semantic model      COMPLETE
I02 Experiment persistence                             COMPLETE
E04 FeatureArtifact v1                                 COMPLETE
F06 Availability / purge / embargo v1                  COMPLETE
F07 Labels / censoring / lockbox v1                    COMPLETE
F08 DSR/PBO robust comparison v1                       COMPLETE
H05 Deterministic historical replay                    COMPLETE
```

Important credited implementation details:

- `quant_platform.validation.walk_forward` already owns `WalkForwardFold`, `WalkForwardScheduleSpec` and `build_walk_forward_folds`.
- `quant_platform.validation.availability` already owns `Embargo`, `DependencyEvidence`, `ValidationCandidate` and `classify_candidate`.
- `quant_platform.experiments.identities` already owns `StudyIdentity`, `TrialIdentity`, `RunSpecIdentity`, `RunIdentity`, `ArtifactContentIdentity` and `ArtifactIdentity`.
- `quant_platform.experiments.persistence` already owns `ExperimentRepository`, run lifecycle persistence and artifact registration.

Wave 5 composes these existing primitives. It must not duplicate them under `quant_platform.learning`.

---

## In-Scope Capability Inventory

| ID | Capability | Owner | Requires | Unlocks | Decision State | Target Impl State | Acceptance / Authority |
| :--- | :--- | :--- | :--- | :--- | :---: | :---: | :--- |
| **I04** | `Supervised input / selection v1` | Learning | `E04`, `F06`, `F07` | `I05` | RESOLVED | COMPLETE | Build supervised input projections from canonical features and labels; reuse validation-owned walk-forward/purge/embargo; fail closed on leakage. |
| **I03** | `Trial accounting / comparison v1` | Experiment System | `I01`, `I02`, `H05` | `I05`, model selection | RESOLVED | COMPLETE | Comparable trial population identity, resume/idempotency and metric comparison through Study / Trial / Run / Artifact persistence. |
| **I05** | `Supervised training / evaluation v1` | Learning | `I03`, `I04` | `J07` | RESOLVED | COMPLETE | Deterministic baseline model training/evaluation, fold-safe normalization, OOF metrics and model/prediction/metric artifacts. |

---

## Decision Gate Clarification: DG-G

`I03`, `I04` and `I05` are `RESOLVED / MISSING` in the capability model. This Wave is therefore implementation and harvest work, not a new decision-gate resolution.

DG-G remains open only for its still-unresolved branches:

- `I06` Strategic RL contract;
- `I07` Execution RL contract;
- `J03` Job runtime.

Do not resolve or implement those branches in Wave 5. Do not infer a job scheduler from experiment persistence, and do not infer RL runtime semantics from supervised ML.

---

## Legacy Harvest Map

Harvest decisions come from `docs/legacy/ADOPTION_LEDGER.md` at legacy baseline `1adf6ba79bcb766c93c6e487017561565ab8c131`.

| Legacy ID | Decision | Wave 5 disposition |
|---|---|---|
| `H09` Explicit selection + leakage guard | ADAPT / P1 | Adapt into `I04` as fail-closed feature/label selection checks tied to canonical availability evidence. |
| `H10` Expanding positional schedule | ADOPT / P2 | Already effectively covered by canonical `validation.walk_forward`; Wave 5 reuses it and may add supervised-composition tests, but must not reimplement it. |
| `H11` Train-fit fold normalization | ADOPT / P2 | Adopt the pure fit/transform/statistics core for `I05`; fit only on training samples, transform without mutating learned statistics. |
| `H12` Sample uniqueness | ADAPT / P2 | Adapt into `I04` over the temporally admissible fold universe only; never compute with future-spanning information. |
| `H13` Supervised wrappers + centroid baseline | ADAPT / P3 | Adapt minimally for deterministic baseline model wrappers and probability-column alignment. External ML dependencies require explicit deterministic dependency policy in the implementing slice. |
| `H15` Trial accounting | ADAPT / P2 | Converge useful attempt-accounting concepts into existing `ExperimentRepository` / Study / Trial / Run semantics for `I03`. |
| `H18` Versioned recipe library | ADAPT / P2 | Out of scope for Wave 5 unless a later issue explicitly selects recipe/version-diff work. |

Known legacy defects listed in `ADOPTION_LEDGER.md` are active refusal criteria. Do not port unchanged code that leaks future data, conflates identity/persistence, uses non-canonical storage, or depends on legacy runtime topology.

---

## Package Boundary and Modular Monolith Updates

Wave 5 introduces a new runtime package:

```text
src/quant_platform/learning/
```

The package-boundary test must register the package explicitly:

```text
OWNERS:
  quant_platform.learning -> "learning"

ALLOWED:
  "learning" -> {"learning", "experiment", "validation", "feature", "shared"}
```

Boundary rules:

- `learning` must not depend on `application`, `strategy`, `execution`, `portfolio`, `replay`, `access`, `producer`, `source_adapters` or `physical`.
- `learning` must not open physical storage directly (`Parquet`, CSV, SQLite, object-store paths, catalog tables outside Experiment persistence).
- `learning` consumes already-canonical logical values and identities: FeatureArtifact / label / validation / experiment identity inputs supplied by its caller or tests.
- DataGateway access and application composition remain outside `learning`.
- Strategy integration remains outside Wave 5. Wave 5 may produce artifacts that future Strategy work can consume, but it must not wire model predictions into live or paper `DecisionIntent` execution.

---

## Active Path

The repository rule of one bounded mutation slice at a time governs execution. The operational order below respects risk and reviewability; it is not a claim that `I04` depends on `I03`.

```text
1. Slice I04 - Supervised Input, Selection and Anti-Leakage
   - Add `quant_platform.learning` package boundary registration.
   - Implement supervised input value models and deterministic projection results.
   - Reuse validation-owned walk-forward / availability / purge / embargo.
   - Adapt H09 and H12.
   - Prove no lookahead leakage with adversarial temporal tests.
        |
        v
2. Slice I03 - Trial Accounting and Comparison
   - Reuse I01/I02 identities and persistence.
   - Adapt H15 into canonical trial accounting/comparable population semantics.
   - Implement deterministic comparison protocol and model-selection result values.
   - Prove resume/idempotency and exact identity conflict handling.
        |
        v
3. Slice I05 - Supervised Training, Evaluation and Artifact Emission
   - Adopt H11 fold normalizer semantics.
   - Adapt H13 deterministic baseline model wrappers.
   - Implement OOF metric calculation and probability-column alignment.
   - Register model, prediction and metric artifacts through ExperimentRepository.
        |
        v
4. Wave 5 Golden E2E Supervised Proof
   - Execute a bounded deterministic supervised pipeline over canonical Bybit BTCUSDT evidence.
   - Prove reproducible folds, selections, metrics and artifact identities across independent runs.
   - Record evidence under `docs/integration/`.
        |
        v
5. Wave 5 Governance Closeout
   - Reconcile `CAPABILITY_DAG.md`, `CAPABILITY_MAP.md`, `ROADMAP.md` and `OPEN_DECISIONS.md`.
   - Promote `implement/wave-5` to `main` only after Golden proof and reconciliation.
```

---

## Acceptance Criteria

Wave 5 is complete only when all of the following are observably true:

1. **No Lookahead Bias**: supervised input construction refuses any feature, label or dependency unavailable at the applicable decision/fold cutoff.
2. **Canonical Validation Reuse**: walk-forward, availability, purge and embargo semantics reuse `quant_platform.validation`; `learning` does not define a competing fold or embargo authority.
3. **Fold-Local Statistics**: every normalizer/scaler fits only on admitted training samples and transforms validation/test samples without mutating fitted statistics.
4. **Deterministic Computation**: fold generation, selection, sample weighting, normalization, model baseline training, prediction ordering and metric aggregation are deterministic for identical inputs.
5. **Experiment Identity and Idempotency**: trial/run accounting uses the existing Study / Trial / Run identity and persistence model; duplicate or resumed attempts are exact and conflict-safe.
6. **Artifact Registration**: model, prediction and metric artifacts use `ArtifactContentIdentity` / `ArtifactIdentity` and are registered through `ExperimentRepository`.
7. **Package Boundary**: `tests/test_package_boundaries_v1.py` passes with `quant_platform.learning` registered and no forbidden dependencies.
8. **Verification Gate**: `python tools/workflow.py preflight`, `python tools/check_markdown_links.py` and the relevant focused tests pass.
9. **Golden Proof**: Wave 5 records a deterministic supervised E2E proof with stable metrics and artifact identities across independent runs.

---

## Out of Scope

Do not pull into Wave 5 unless a later authorized scope explicitly changes it:

- Strategic RL (`I06`) and execution RL (`I07`).
- Job runtime / asynchronous distributed execution (`J03`).
- Paper/shadow trading mode (`J07`) or live product mode (`J08`).
- Live broker connections, exchange order placement or live trading authorization.
- Live consumer cursor `B06`.
- API transport (`J02`) or UI/TUI clients (`J04`-`J06`).
- Multi-asset execution (`H06`).
- Versioned recipe library / recipe diffing (`H18`) unless separately scoped.
- Generic model registry, hyperparameter search infrastructure or arbitrary plugin framework beyond what `I03`-`I05` require.

---

## Stop / Escalation Conditions

Stop and report rather than implement if:

- an external ML dependency is required but cannot be made deterministic under pinned versions, fixed seeds and controlled threading;
- a legacy harvest candidate needs future data or post-fold information that cannot be represented through canonical availability evidence;
- `learning` needs direct physical storage access, direct catalog reads outside Experiment persistence, or Application-owned composition;
- supervised outputs require Strategy, Execution, Paper/Shadow or Live runtime semantics to be useful;
- artifact payload storage requirements exceed the existing Experiment artifact registration boundary and need a new storage authority.
