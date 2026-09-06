# Scope: Producer–Consumer Conformity Implementation Gate v1

## Goal

Record the completed executable conformity seam between the Data Plane producer
output and the DataGateway consumer input, using the accepted
`PRODUCER_CONSUMER_CONFORMITY.md` contract as the semantic authority. The
Contract Freeze Gate and Conformity Implementation Gate have passed. This
closeout concludes the controlled convergence cycle; it does not redesign the
product roadmap or architecture.

## Current state and roadmap position

**Phase A — Controlled Bidirectional Convergence is COMPLETE**:

- Contract Freeze Gate: **PASSED**;
- Conformity Implementation Gate: **PASSED**;
- Human vertical / Golden Bybit BTCUSDT E2E: **PASS**;
- Adversarial Acceptance A1–A9: **PASS**;
- Candle Ordering Compatibility: **PASS**;
- Final Conformity Gate Review: **APPROVE** with `BLOCKERS: NONE` and
  `IMPORTANT: NONE`;
- temporary Producer/Consumer lockstep: **REMOVED**.

Completed conformity slices and acceptance milestones on authoritative
`origin/main`:

1. Shared Semantic Primitives v1 — **DONE** (`RecordTimeBounds`,
   `CanonicalContentHashV1`, and provider-supplied ordering compatibility);
2. Canonical Parquet Materializer v1 — **DONE**;
3. Bounded DataGateway finite read v1 — **DONE**;
4. Manifest + Coverage Emission v1 — **DONE**;
5. Golden Conformity Acceptance Support v1 — **DONE** as reusable gate-support
   infrastructure; it is not the Golden E2E proof and does not imply Gate PASS;
6. Publication Certification runtime v1 — **DONE** for frozen S13 Phases 1–3:
   SEAL to durable `state='closed'`, authoritative CERTIFY, and durable
   `quality_reports` evidence recording;
7. Publication Eligibility Bridge v1 — **DONE** for frozen S14 / S13.5 Phases
   4–5: current certification evidence drives `closed → valid/degraded` under
   the frozen first-vertical rules and Phase-5 post-write verification fails
   closed;
8. Human vertical / Golden Bybit BTCUSDT E2E — **DONE / PASS** for UTC day
   `2024-01-15`, with an exact Golden match through `DataGateway.scan()`.
9. Adversarial Acceptance A1–A9 — **DONE / PASS**;
10. Candle Ordering Compatibility — **DONE / PASS**;
11. Final Conformity Gate Review — **DONE / APPROVE**, all eight Decision §7
    exit criteria PASS.

The Human E2E branch also closes two integration gaps discovered while
preparing the real vertical: accepted ADR-0025 plus `dataset-manifest-v2`
represent direct source/archive → canonical acquisition without a fake raw
parent, and the schema-registry bootstrap establishes `trade-v1` from
authoritative repository bytes. Neither remediation changes the frozen v1
semantics.

The exact Human E2E observations and durable identities are recorded in
[`docs/integration/HUMAN_GOLDEN_E2E_BYBIT_BTCUSDT_2024-01-15.md`](docs/integration/HUMAN_GOLDEN_E2E_BYBIT_BTCUSDT_2024-01-15.md).

The completed Gate path is:

```text
Human Golden E2E PASS → governance closeout → branch review / CI → merge
    → Adversarial Acceptance PASS
    → Candle Ordering Compatibility PASS
    → Final Conformity Gate Review APPROVE
    → Conformity Implementation Gate PASSED
```

The Human E2E used operator/bootstrap authorization sufficient to exercise the
publication path. That is acceptance evidence only and does not define the
production runtime authorization model. **Server Access & Runtime Identity
Hardening v1** is tracked as a separate, non-blocking operational follow-up in
`docs/architecture/OPEN_DECISIONS.md`.

The post-gate model is **Phase B — Full Bidirectional Expansion**. The
temporary Producer–Consumer lockstep is removed and Producer and Consumer may
resume independent,
concurrent development subject to normal contracts, ownership, dependency
direction, architecture gates and explicit scopes. This does not remove any
architecture constraint.

The next work is not started by this closeout:

```text
NEXT EXPLICIT SCOPE:
Package Boundary / Modular Monolith Foundation v1
        ↓
Legacy Capability Harvest Audit v1
        ↓
Broad independent Producer / Consumer expansion
```

## Completed scope

- the completed first-vertical publication certification and eligibility flow,
  from S13 SEAL/CERTIFY/RECORD EVIDENCE through S14 PUBLISH ELIGIBILITY/VERIFY,
  as the automated publication baseline to be exercised end to end;
- closeout and review of the completed Human Golden E2E proof;
- required adversarial acceptance, including zero-event coverage, gaps,
  supersession, overlap, relocation, rebuild, physical-layout variation and
  abort-before-completion;
- CandleDefinition ordering-compatibility acceptance;
- the evidence, tests and review that established Conformity Implementation
  Gate PASS;
- documentation that records current status without changing frozen semantics.

The implementation dependency order and normative requirements are frozen in
`docs/contracts/PRODUCER_CONSUMER_CONFORMITY.md` §23. Shared/core remains
source-neutral; source-specific components depend toward generic/core, never
the inverse. Quantitative business logic remains outside App UI, TUI and CLI
clients, and upper layers do not bypass the DataGateway boundary.

## Controlled convergence result

The temporary controlled-convergence rule is removed because the Conformity
Implementation Gate has passed. Independent Producer and Consumer work remains
subject to explicit scopes, frozen contracts, ownership and dependency
direction. Package Boundary / Modular Monolith Foundation v1 is the next
explicit scope and has not started.

## Unimplemented post-Gate capabilities

- full Candle runtime — no longer blocked by the Conformity Gate, still not
  implemented;
- Feature runtime and broader Representation expansion;
- broader producer verticals;
- live ingest and live collection;
- multi-venue runtime;
- L1, L2, L3/MBO runtime;
- unrelated consumer or producer vertical expansion;
- broader paper/live operational mechanics.

Maintenance, documentation and tests may proceed when they do not weaken a
frozen contract or bypass the current scope boundary.

## Package topology boundary

This closeout performs no package restructuring. The current
single-repository, single-`src/` topology remains valid until the next explicit
scope begins.

The next explicit structural scope is **Package Boundary / Modular Monolith
Foundation v1**, before broad independent Producer/Consumer expansion. This is
a temporary cross-cutting checkpoint, not a new product phase. Exact package
names, hierarchy, deployment split and migration mechanics remain open until
that audit.

Immediately after that structural checkpoint, run **Legacy Capability Harvest
Audit v1** before broad bidirectional expansion. Legacy code remains evidence
only and is classified capability-by-capability as ADOPT / ADAPT / REVIEW /
REJECT against current canonical semantics.

## Preserved future roadmap

The following work remains planned and is not deleted or made permanently
sequential by this scope.

### Producer roadmap after the conformity gate

- capacity monitoring;
- storage health and pressure behavior;
- RAW/source protection;
- safe storage placement and relocation;
- backup and restore;
- backup verification;
- retention/deletion authority;
- backfill and repair;
- live trades pilot;
- multi-venue trades;
- L1;
- L2;
- L3/MBO;
- advanced recovery and reconciliation.

### Consumer/Product roadmap after the conformity gate

- Candle runtime / Representation foundation;
- `FeatureDefinition` / `FeatureSetDefinition` / `FeatureArtifact`;
- Research / Hypothesis / Event / Outcome;
- Validation, labeling, warmup, walk-forward, purge, embargo, lockbox and
  censoring;
- Strategy / `DecisionIntent` / risk / sizing;
- Execution / replay / portfolio;
- Unified Experiment Orchestration and Persistence;
- Canonical API and job runtime;
- Supervised ML;
- Strategic RL;
- Execution RL;
- Clients: CLI / TUI / App UI;
- Paper / shadow;
- Live operation.

## Out of scope for this governance closeout

- changing `trade-v1`, dataset/partition/coverage contracts, CandleDefinition,
  DataGateway, Consumer API or any accepted ADR semantics;
- weakening or replacing frozen invariants;
- modifying runtime code, tests, schemas, DDL or fixtures as part of this
  documentation pass;
- implementing any remaining runtime slice while synchronizing these files;
- creating a parallel architecture or a generic framework;

## Completion criteria

- this file records the concluded Conformity Implementation scope and Gate
  state;
- all conformity slices, Human Golden, adversarial, ordering and final-review
  milestones are explicit and complete;
- Phase A is complete and the temporary lockstep is removed;
- Package Boundary is the next explicit scope, followed by Legacy Capability
  Harvest, before broad expansion;
- unimplemented future Producer/Consumer capabilities remain explicit;
- the governance documents are factually aligned with the current branch and
  its `origin/main` baseline;
- `python tools/check_markdown_links.py` and `git diff --check` pass;
- no runtime code, tests, schemas, DDL, fixtures or frozen normative semantics
  are changed by this synchronization.
