# ADR-0024 — Package Boundary / Modular Monolith Foundation v1

**Status:** ACCEPTED

**Date:** 2026-08-31

## Context

The current repository is a single authoritative Python codebase with one
source root, `src/`, and one `quant_platform` namespace. The remaining
Producer–Consumer Conformity work is still completing the shared seam: the
manifest and coverage path, certification, publication, the Golden Bybit
vertical and the required acceptance evidence.

The current topology is sufficient to finish that vertical, but generic
responsibilities currently coexist under packages such as
`quant_platform.data`. A broad package move before the Golden vertical is
complete would create import, test and documentation churn without proving a
capability. It would also risk encoding package boundaries from assumptions
rather than from observed runtime ownership.

Conversely, allowing broad Producer and Consumer expansion immediately after
the Conformity Implementation Gate would make a later ownership correction
more expensive. A structural checkpoint is therefore needed between the
completed conformity proof and unrestricted horizontal expansion.

This ADR records the timing and direction of that checkpoint. It does not
define the final package tree.

## Decision

### 1. No broad package restructure before the Conformity Implementation Gate

Until the Conformity Implementation Gate passes, Quant Platform preserves the
current runtime and package topology sufficiently to finish and prove the
Producer–Consumer conformity vertical. No broad move, rename or refactor of
existing runtime packages is authorized merely to improve long-term
organization.

The priority remains:

```text
canonical production
  → manifests / coverage
  → certification
  → publication
  → catalog
  → bounded DataGateway
  → Golden E2E
  → adversarial and compatibility acceptance
  → Conformity Implementation Gate
```

### 2. Mandatory post-gate structural checkpoint

Immediately after Conformity Implementation Gate PASS, and before broad
independent Producer/Consumer expansion, the repository must execute this
dedicated architectural slice:

**Package Boundary / Modular Monolith Foundation v1**

It is a structural checkpoint, not a permanent product layer and not a new
numbered roadmap phase. The program order is:

```text
Controlled Conformity Convergence
            ↓
Conformity Implementation Gate
            ↓
Package Boundary / Modular Monolith Foundation v1
            ↓
Full independent bidirectional expansion
```

The checkpoint establishes scalable bounded package ownership before major
horizontal expansion into Candle runtime and Representations, Features,
Research, Outcomes and Validation, Strategy, Execution and Portfolio,
Experiments, API and Jobs, Producer operations, live ingest, multi-venue and
L1/L2/L3/MBO work.

### 3. Modular monolith is the default target

Unless later repository evidence justifies another deployment or package
model, Quant Platform remains:

- one authoritative repository;
- one coherent product codebase;
- one Python source root, `src/`;
- one overall `quant_platform` namespace/codebase model.

A single repository or a single `src/` root is not an architectural defect.
The problem to prevent is the indefinite accumulation of unrelated
responsibilities under generic catch-all packages such as
`quant_platform.data`.

### 4. Bounded contexts must become structural

The post-gate checkpoint must identify and establish package boundaries that
reflect ownership and dependency direction. Important dependency rules must
become mechanically testable through architecture tests.

At minimum, the future structure must be capable of enforcing that:

- source-specific code may depend on generic/shared abstractions, never the
  inverse;
- upper layers do not bypass DataGateway to open canonical storage;
- clients and transport layers do not own quantitative domain logic;
- Representations, Features, Research, Outcomes, Validation, Strategy,
  Execution and Portfolio retain distinct ownership;
- adding a second venue/provider does not require modifying generic core solely
  to introduce venue-specific semantics.

### 5. Exact package names remain open

This ADR does not mandate an exact tree or exact future package names.
Illustrative names such as `data_plane`, `data_access`, `representations`,
`features`, `research` and `execution` are not frozen decisions. Exact names,
hierarchy and migration boundaries are deferred until the post-gate structural
audit can use evidence from the completed Golden vertical.

### 6. Deployment and distribution split remain open

The following remain intentionally undecided:

- whether executable hosts eventually live under `apps/`;
- whether API, worker or client SDK become independently installable packages;
- whether any component later becomes an independently deployed service;
- whether multiple Python distribution packages are warranted;
- the exact migration mechanics for existing `quant_platform.data` modules;
- the exact package dependency graph;
- the precise migration sequence and compatibility policy.

The default remains a modular monolith. Future evidence may justify evolution
through a later ADR.

## Rationale

### Why not restructure now

- conformity runtime is still incomplete;
- the materializer and bounded DataGateway have only recently converged on
  `main`;
- moving packages now creates import, test and documentation churn without
  adding a capability;
- at acceptance time, the Golden E2E had not provided complete evidence about
  natural runtime seams;
- premature package design risks encoding guessed rather than observed
  bounded contexts.

### Why not wait too long

After the Conformity Implementation Gate, the roadmap expects concurrent
expansion into the following areas:

Producer: capacity, health and pressure monitoring; RAW/source protection;
storage relocation; backup and restore; backfill and repair; live acquisition;
multi-venue; L1/L2/L3/MBO; recovery and reconciliation.

Consumer/Product: Candle runtime; Representations; Feature Engine;
Research/Outcomes; Validation; Strategy; Execution/Portfolio; Experiments;
API/job runtime; ML/RL; clients.

Allowing that expansion before package ownership is explicit would make later
structural correction substantially more expensive.

## Consequences

Positive consequences:

- the current conformity critical path remains stable;
- package topology is redesigned using evidence from a working vertical;
- broad expansion begins from explicit bounded contexts;
- dependency direction can be structurally tested;
- monorepo simplicity is retained;
- premature microservice and multi-package complexity is avoided.

Costs and trade-offs:

- `quant_platform.data` remains temporarily broader than desired;
- short-lived organizational debt is consciously accepted;
- the post-gate structural checkpoint is mandatory before broad expansion;
- some import churn is deferred rather than eliminated.

## Non-goals

This ADR does not:

- restructure or rename packages now;
- change DataGateway semantics or Producer–Consumer Conformity semantics;
- create a new product layer or numbered roadmap phase;
- introduce microservices, multiple repositories or multiple source roots;
- decide API deployment, process topology or executable host placement;
- authorize broader vertical work before the Conformity Implementation Gate;
- freeze exact package names, hierarchy or migration mechanics.

## Governance and review

This ADR records the accepted project direction. Its decision boundary
<!-- Historical pre-acceptance wording retained for audit only.
remain **PROPOSED — pending independent architecture review** until an
independent reviewer confirms the decision boundary and its preservation of the
open topology and deployment choices.
-->

The accepted Conformity contract and ADR-0023 remain the semantic and
governance authorities for the conformity seam. This ADR does not reinterpret
or weaken either one.

## Acceptance provenance

The independent architecture review returned **APPROVE** with
`BLOCKERS: NONE`. This records review provenance for the accepted ADR; it does
not create a new governance mechanism or alter the decision above.

## Implementation outcome — Package Boundary / Modular Monolith Foundation v1

This section records the **disposition** of the checkpoint this ADR mandated,
using the evidence section 5 deferred it to. It is an implementation outcome,
not new frozen semantics: it does not freeze a future package tree, does not
create a new contract, and does not reinterpret or weaken the Conformity
contract or ADR-0023.

The checkpoint was executed on `implementation/package-boundary-foundation-v1`
from baseline `9c938a5`, with implementation candidate `745de0f`. It is not
`COMPLETE` until that candidate, its independent review, CI and merge to
authoritative `origin/main` are closed; the active scope is tracked in
`SCOPE.md`.

### Resolved by this slice

- **Access package name.** The Data Access bounded context is
  `quant_platform.access`. It owns the DataGateway, the read catalog and the
  Access request/result/locator models. The illustrative names listed in
  section 5 (`data_plane`, `data_access`) were not adopted.
- **Shared and Producer placement.** Shared canonical primitives and the
  Producer modules remain under `quant_platform.data`. The generic package was
  narrowed rather than moved: its façade no longer eagerly loads Access or
  Producer runtimes.
- **Physical seam.** `quant_platform.data.parquet` remains a deliberate shared
  physical seam, used by both the Access read path and the Producer write path
  and owned as `physical` rather than as Producer orchestration.
- **Source-specific placement.** `quant_platform.source_adapters` remains
  source-specific and was not restructured.
- **Mechanical enforcement.** The dependency rules required by section 4 are
  now executable rather than documentary:

```text
shared !→ source-specific
producer !→ access
access !→ producer orchestration/publication
```

  `tests/test_package_boundaries_v1.py` resolves absolute and relative imports,
  aliases, re-exports and package initialization, refuses star and dynamic
  imports rather than leaving them untracked, requires every runtime module to
  carry an explicit ownership decision, and asserts that the shared façade does
  not eagerly load Access or Producer runtimes.
- **Migration mechanics for the current `quant_platform.data` modules.**
  Performed by extraction of the Access package with explicit call-site
  migration and no compatibility shim reintroducing a catch-all.

### Deliberately not exercised, and still open

This slice touched only the Data Access seam. Section 6 remains in force for
everything it did not exercise:

- executable host placement, including any future `apps/` layout;
- independently installable API, worker or client packages;
- service/deployment split and process topology;
- multiple Python distribution packages;
- bounded-context and package names beyond the Data Access seam, including
  Representations, Features, Research, Outcomes, Validation, Strategy,
  Execution and Portfolio.

The default remains a modular monolith. Future evidence may justify evolution
through a later ADR.
