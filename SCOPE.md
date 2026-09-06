# Scope: Legacy Capability Harvest Audit v1 — COMPLETE

## Goal

Close the mandatory post-Gate Legacy Capability Harvest Audit checkpoint using the accepted read-only audit evidence, and hand off the next explicit implementation slice without changing runtime, contracts, schemas or architecture semantics.

Legacy repositories remain reference/evidence libraries only. They are never runtime dependencies of the canonical platform.

## Cycle state

```text
canonical baseline       = 7f9b8dc9c5671908c5580c525705c42db7263dc7
legacy repository        = NeoNix-Lab/ml_core
legacy baseline          = 1adf6ba79bcb766c93c6e487017561565ab8c131
audit                    = COMPLETE
mutation during audit    = NONE
tests during audit       = NOT EXECUTED
scope state              = COMPLETE
```

The audit verified both exact Git baselines and read evidence from those objects rather than from working-tree state.

## Completed predecessor checkpoints

```text
Contract Freeze Gate                              = PASSED
Conformity Implementation Gate                    = PASSED
Package Boundary / Modular Monolith Foundation   = COMPLETE
Legacy Capability Harvest Audit v1               = COMPLETE
```

Package Boundary established the current modular-monolith ownership and dependency rules:

```text
quant_platform.access
  owns Gateway / read catalog / Access models

quant_platform.data
  owns shared canonical primitives + Producer

quant_platform.data.parquet
  remains the deliberate shared physical seam

quant_platform.source_adapters
  remains source-specific
```

```text
shared !→ source-specific
producer !→ access
access !→ producer orchestration/publication
```

Those rules remain unchanged by this audit.

## Audit outcome

The accepted final capability classification is recorded in `docs/legacy/ADOPTION_LEDGER.md`.

```text
capabilities assessed = 31
ADOPT                = 3
ADAPT                = 20
REVIEW               = 1
REJECT               = 6
SUPERSEDED           = 1
```

Harvest priority:

```text
P1 = H01, H04, H09, H17
P2 = H02, H03, H05, H06, H07, H08, H10, H11, H12, H15, H16, H18, H19, H20
P3 = H13, H21, H22, H23, H24
```

The only REVIEW residue is H14 — DSR/PBO estimator semantics. It is non-blocking for the selected first harvest slice.

## Selected next implementation slice

The audit selected exactly one first implementation candidate:

```text
H01 — pure diagonal / stacked imbalance core
classification = ADOPT
priority       = P1
canonical owner = Feature Engine / Footprint
```

The ADOPT boundary is intentionally narrow:

```text
_ratio
compute_diagonal_imbalance
compute_stacked_imbalance
```

The following are explicitly outside that ADOPT boundary:

```text
compute_bar_flow
stacked_imbalance_zone
legacy registry / engine / orchestration
legacy package topology
```

Legacy numeric and edge-case evidence for H01 is credited where it proves the same quantitative properties. The audit does **not** claim that production integration is already proven.

A future H01 implementation scope must still establish the minimum canonical connection to:

```text
canonical input / grain
validated tick grid
temporal availability
FeatureDefinition provenance
```

Selecting H01 therefore does not declare a complete Feature Engine, Footprint runtime, provider framework or materialization layer implemented.

## H14 REVIEW residue

H14 remains unresolved only on the exact Evaluation semantics of DSR/PBO:

```text
precise estimator definitions
input return series
trial population
comparable fold semantics
reference numeric vectors
```

This residue does not block H01 or other independently authorized post-Gate slices that do not depend on DSR/PBO.

## Expansion state

The mandatory cross-cutting checkpoints are now complete:

```text
Conformity Implementation Gate PASSED
        ↓
Package Boundary Foundation COMPLETE
        ↓
Legacy Capability Harvest Audit v1 COMPLETE
        ↓
broad independent Producer / Consumer expansion UNLOCKED
```

`UNLOCKED` does not mean architecture-unconstrained or automatically active. Each implementation capability still requires an explicit bounded scope and must respect frozen contracts, ownership, dependency direction and authorization boundaries.

## Next scope

```text
H01 — Diagonal / Stacked Imbalance Core
```

H01 is the next selected implementation slice. It is not implemented by this governance closeout and is not ACTIVE until its own explicit implementation scope/mandate is opened.

## Out of scope for this closeout

- any changes under `src/`, `tests/`, `tools/`, `schemas/`, `db/` or `fixtures/`;
- implementation of H01 or any other legacy capability;
- resolution of H14 DSR/PBO semantics;
- creation of a generic provider/plugin framework;
- migration of legacy runtime topology;
- server/database work;
- Human Golden E2E;
- changes to accepted contracts, ADR semantics, schemas, DDL or fixtures.

## Completion criteria

This governance closeout is complete when:

- `docs/legacy/ADOPTION_LEDGER.md` records the accepted 31-capability audit result and exact evidence baselines;
- H14 remains an exact non-blocking REVIEW residue;
- H01 is recorded as the first selected harvest slice with its narrow ADOPT boundary and remaining production proposition;
- `docs/product/ROADMAP.md` records Legacy Capability Harvest Audit v1 as COMPLETE and broad independent expansion as unlocked;
- runtime and semantic contract delta remain NONE.
