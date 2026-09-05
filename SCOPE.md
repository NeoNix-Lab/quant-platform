# Scope: Producer–Consumer Conformity Implementation Gate v1

## Goal

Complete the executable conformity seam between the Data Plane producer output
and the DataGateway consumer input, using the accepted
`PRODUCER_CONSUMER_CONFORMITY.md` contract as the semantic authority. The
Contract Freeze Gate has passed. This cycle is the controlled convergence
period before the Conformity Implementation Gate; it is not a redesign of the
product roadmap or architecture.

## Current state and roadmap position

The repository is in **Phase A — Controlled Bidirectional Convergence**:

- Contract Freeze Gate: **PASSED**;
- Conformity Implementation Gate: **IN PROGRESS / PARTIAL**;
- Human vertical / Golden Bybit BTCUSDT E2E: **PASS**;
- Producer and Consumer may progress bidirectionally and concurrently only on
  work that directly advances the shared conformity seam and its gate evidence.

Completed conformity slices in the current branch; items 1–7 are already on
the authoritative `origin/main` baseline:

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

The Human E2E branch also closes two integration gaps discovered while
preparing the real vertical: accepted ADR-0025 plus `dataset-manifest-v2`
represent direct source/archive → canonical acquisition without a fake raw
parent, and the schema-registry bootstrap establishes `trade-v1` from
authoritative repository bytes. Neither remediation changes the frozen v1
semantics.

The exact Human E2E observations and durable identities are recorded in
[`docs/integration/HUMAN_GOLDEN_E2E_BYBIT_BTCUSDT_2024-01-15.md`](docs/integration/HUMAN_GOLDEN_E2E_BYBIT_BTCUSDT_2024-01-15.md).

Remaining critical path to the Conformity Implementation Gate review:

9. Adversarial acceptance;
10. Candle ordering compatibility acceptance;
11. final Conformity Implementation Gate review.

The branch closeout path is intentionally separate from the remaining gate
path:

```text
Human Golden E2E PASS → governance closeout → branch review / CI → merge

from main:
Adversarial Acceptance + Candle Ordering Compatibility
    → Final Conformity Gate Review
    → Conformity Implementation Gate PASS
```

The Human E2E used operator/bootstrap authorization sufficient to exercise the
publication path. That is acceptance evidence only and does not define the
production runtime authorization model. **Server Access & Runtime Identity
Hardening v1** is tracked as a separate, non-blocking operational follow-up in
`docs/architecture/OPEN_DECISIONS.md`.

The post-gate model is **Phase B — Full Bidirectional Expansion**. After the
Conformity Implementation Gate passes, the temporary Producer–Consumer
lockstep is removed and Producer and Consumer may resume independent,
concurrent development subject to normal contracts, ownership, dependency
direction, architecture gates and explicit scopes. This does not remove any
architecture constraint.

## In scope

- the completed first-vertical publication certification and eligibility flow,
  from S13 SEAL/CERTIFY/RECORD EVIDENCE through S14 PUBLISH ELIGIBILITY/VERIFY,
  as the automated publication baseline to be exercised end to end;
- closeout and review of the completed Human Golden E2E proof;
- required adversarial acceptance, including zero-event coverage, gaps,
  supersession, overlap, relocation, rebuild, physical-layout variation and
  abort-before-completion;
- CandleDefinition ordering-compatibility acceptance;
- the evidence, tests and review needed for Conformity Implementation Gate
  PASS;
- documentation that records current status without changing frozen semantics.

The implementation dependency order and normative requirements are frozen in
`docs/contracts/PRODUCER_CONSUMER_CONFORMITY.md` §23. Shared/core remains
source-neutral; source-specific components depend toward generic/core, never
the inverse. Quantitative business logic remains outside App UI, TUI and CLI
clients, and upper layers do not bypass the DataGateway boundary.

## Controlled convergence rule

Until the Conformity Implementation Gate passes, broader vertical expansion is
blocked. Producer and Consumer work is allowed only when it directly advances
one of the conformity slices or its gate evidence. The completed slices do not
constitute Conformity Implementation Gate PASS by themselves.

## Explicitly blocked until Conformity Implementation Gate PASS

- full Candle runtime;
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

Current Conformity scope: broad package restructuring is **OUT OF SCOPE**.
The current single-repository, single-`src/` topology remains valid while the
remaining gate evidence is completed.

Immediately after Conformity Implementation Gate PASS, the next structural
scope is **Package Boundary / Modular Monolith Foundation v1**, before broad
independent Producer/Consumer expansion. This is a temporary cross-cutting
checkpoint, not a new product phase. Exact package names, hierarchy,
deployment split and migration mechanics remain open until that audit.

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
- branch creation/switching as part of this documentation synchronization.

## Completion criteria

- this file describes the active Conformity Implementation scope and current
  gate state;
- the seven completed slices/support capabilities and the remaining critical
  path are explicit;
- Human vertical / Golden Bybit BTCUSDT E2E is recorded as **PASS** without
  promoting the complete gate;
- adversarial acceptance, Candle ordering compatibility and final gate review
  remain explicit residual blockers;
- branch review/CI/merge is distinct from Conformity Gate PASS and post-gate
  work;
- pre-gate controlled convergence and post-gate independent concurrency are
  explicit;
- blocked broader work and preserved future Producer/Consumer roadmap items
  are explicit;
- the governance documents are factually aligned with the current branch and
  its `origin/main` baseline;
- `python tools/check_markdown_links.py` and `git diff --check` pass;
- no runtime code, tests, schemas, DDL, fixtures or frozen normative semantics
  are changed by this synchronization.
